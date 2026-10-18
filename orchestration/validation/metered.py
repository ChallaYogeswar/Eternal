"""Measurement wrappers: count what a provider actually does (calls, latency, tokens, 429s, errors) and pace
requests so a free tier is not hammered. Neither wrapper changes what a model says."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from eternal_core.llm.base import LLMAuthError, LLMError, LLMProvider, LLMResponseError, LLMResult, LLMUnavailableError

from ..model_policy import ModelUnavailable, PolicyRouter
from ..providers.base import LLMRateLimitError
from ..runtime import ActionParseError, parse_action


class CallBudgetExhausted(LLMUnavailableError):
    pass


_TRANSIENT = re.compile(r"server error: HTTP 5\d\d|unreachable|timed? ?out|connection (reset|refused|aborted)|"
                        r"temporarily|high demand|overloaded", re.I)


@dataclass
class ProviderStats:
    calls: int = 0
    ok: int = 0
    rate_limited: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usage_reported: int = 0
    latency_s: List[float] = field(default_factory=list)
    last_retry_after: Optional[float] = None
    error_samples: List[str] = field(default_factory=list)
    bad_replies: List[str] = field(default_factory=list)     # raw model text the strict parser rejected
    bad_reply_count: int = 0
    kinds: Dict[str, int] = field(default_factory=dict)      # rate_limit | auth | response | unavailable | budget

    def bump(self, kind: str) -> None:
        self.kinds[kind] = self.kinds.get(kind, 0) + 1

    def sample(self, kind: str, e: Exception) -> None:
        if len(self.error_samples) < 40:
            self.error_samples.append(f"[{kind}] {type(e).__name__}: {e}"[:300])


def error_kind(e: Exception) -> str:
    if isinstance(e, LLMRateLimitError):
        return "rate_limit"
    if isinstance(e, LLMAuthError):
        return "auth"
    if isinstance(e, LLMResponseError):
        return "response"
    return "unavailable"


class MeteredProvider(LLMProvider):
    """Wraps a provider; `max_calls` is a hard ceiling on real requests for the whole session."""

    def __init__(self, inner: LLMProvider, max_calls: Optional[int] = None):
        self.inner, self.max_calls = inner, max_calls
        self.name = inner.name
        self.stats = ProviderStats()

    def is_configured(self) -> bool:
        return self.inner.is_configured()

    def generate(self, system_prompt: str, messages: List[Dict[str, str]]) -> LLMResult:
        s = self.stats
        if self.max_calls is not None and s.calls >= self.max_calls:
            s.bump("budget")
            raise CallBudgetExhausted(f"--max-calls {self.max_calls} reached")
        s.calls += 1
        start = time.time()
        try:
            r = self.inner.generate(system_prompt, messages)
        except LLMError as e:
            kind = error_kind(e)
            s.bump(kind)
            s.sample(kind, e)
            if kind == "rate_limit":
                s.rate_limited += 1
                s.last_retry_after = getattr(e, "retry_after", None)
            else:
                s.errors += 1
            raise
        s.ok += 1
        try:
            parse_action(r.text)
        except ActionParseError as pe:
            s.bad_reply_count += 1
            if len(s.bad_replies) < 6:
                s.bad_replies.append(f"[{pe}] {r.text[:300]!r}")
        s.latency_s.append(time.time() - start)
        i, o = getattr(r, "input_tokens", None), getattr(r, "output_tokens", None)
        if isinstance(i, int) and isinstance(o, int):
            s.input_tokens += i
            s.output_tokens += o
            s.usage_reported += 1
        else:
            s.input_tokens += (len(system_prompt) + sum(len(m["content"]) for m in messages)) // 4
            s.output_tokens += len(r.text) // 4
        return r


class PacedRouter:
    """Same `.generate` signature as PolicyRouter. When every provider is rate limited it WAITS (bounded) and
    retries instead of failing the case, so a slow free tier produces results rather than noise. Sleeping
    happens here, inside the call, so harness budgets must allow `per_call_timeout_s` >= max_wait."""

    def __init__(self, router: PolicyRouter, metered: List[MeteredProvider], *, max_wait_s: float = 70.0,
                 max_retries: int = 6, sleep: Callable[[float], None] = time.sleep,
                 max_transient_retries: int = 3, transient_backoff_s: float = 5.0):
        self.router, self.metered = router, metered
        self.max_wait_s, self.max_retries, self.sleep = max_wait_s, max_retries, sleep
        self.max_transient_retries, self.transient_backoff_s = max_transient_retries, transient_backoff_s
        self.waited_s = 0.0
        self.retries = 0
        self.transient_retries = 0          # 5xx / network blips retried with backoff (e.g. Gemini "high demand" 503)
        self.quota_exhausted = False        # a wait longer than max_wait_s was required (daily quota)

    @staticmethod
    def _is_transient(e: ModelUnavailable) -> bool:
        """True when every failed attempt was a server-side or network blip (5xx, unreachable, timeout) that a short
        wait can cure. Auth failures, budget exhaustion and policy blocks are never transient."""
        errs = [a.get("error", "") for a in e.attempts if not a.get("ok") or a.get("error")]
        if not errs:
            return False
        for err in errs:
            if "auth failed" in err or "--max-calls" in err or "blocked_by_policy" in err:
                return False
        return all(_TRANSIENT.search(err) for err in errs if not err.startswith("skipped"))

    def _wait_hint(self, e: ModelUnavailable) -> Optional[float]:
        """Seconds to wait if this failure is purely rate limiting; None if retrying cannot help."""
        hints: List[float] = []
        for a in e.attempts:
            err = a.get("error", "")
            if "auth failed" in err:
                return None
            if a.get("skipped") == "rate_limit":
                hints.append(float(a.get("wait_s", 1.0)))
            elif "rate limited" in err:
                ra = max((m.stats.last_retry_after or 0.0) for m in self.metered) if self.metered else 0.0
                hints.append(ra or 15.0)
        return min(hints) if hints else None

    def generate(self, *args, **kwargs):
        transient = 0
        for attempt in range(self.max_retries + self.max_transient_retries + 1):
            try:
                return self.router.generate(*args, **kwargs)
            except ModelUnavailable as e:
                wait = self._wait_hint(e)
                if wait is None and self._is_transient(e) and transient < self.max_transient_retries:
                    delay = min(self.transient_backoff_s * (2 ** transient), self.max_wait_s)
                    transient += 1
                    self.transient_retries += 1
                    self.waited_s += delay
                    self.sleep(delay)
                    continue
                if wait is None or attempt >= self.max_retries + transient:
                    raise
                if wait > self.max_wait_s:
                    self.quota_exhausted = True     # waiting minutes cannot help; the caller should stop and resume later
                    raise
                wait = min(max(wait, 1.0), self.max_wait_s)
                self.waited_s += wait
                self.retries += 1
                self.sleep(wait)
        raise AssertionError("unreachable")
