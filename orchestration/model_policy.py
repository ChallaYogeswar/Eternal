"""
Label-aware model routing (plan s3 "Model router", s6.4, s6.10).

`AgentSpec.model_policy` is only an upper bound. On EVERY call the router checks the live context
labels: cloud is used only if the spec allows it AND the context is cloud-eligible (not secret, not
PII). A local failure falls through to cloud only when both hold — never silently otherwise. Cloud
calls reserve spend atomically before the call so concurrent runs cannot jointly overshoot the cap.

P1 additions: every cloud provider is a `CloudEntry` carrying its own DATA CEILING (a free tier whose
inputs may be used for training is PUBLIC-only), its own price (input/output per million tokens) and
its own rate limits (rpm/rpd/tpm/tpd). Spend is reserved at the worst case and settled with the
provider-reported usage when available. Free entries never touch the spend ledger.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple, Union

from eternal_core.llm.base import LLMError, LLMProvider

from .llm_errors import LLMBadOutput

from .contracts import Labels, ModelPolicy, Sensitivity


class ModelUnavailable(Exception):
    def __init__(self, reason: str, attempts: Optional[List[dict]] = None):
        super().__init__(reason)
        self.reason = reason
        self.attempts = attempts or []


class SpendCapExceeded(Exception):
    pass


@dataclass
class ModelReply:
    text: str
    provider: str
    model: str
    tier: str                       # "local" | "cloud"
    attempts: List[dict] = field(default_factory=list)
    input_tokens: Optional[int] = None      # provider-reported usage, None when not reported
    output_tokens: Optional[int] = None
    cost_usd: float = 0.0                   # settled cost of this call (0 for local and free entries)

    @property
    def external_request(self) -> bool:
        return self.tier == "cloud"


def cloud_eligibility(labels: Labels, policy: ModelPolicy,
                      ceiling: Optional[Sensitivity] = None) -> Tuple[bool, str]:
    """Global cloud rule, optionally tightened by a specific provider's data ceiling."""
    if policy != ModelPolicy.CLOUD_ALLOWED:
        return False, "agent policy is local_only"
    if labels.sensitivity >= Sensitivity.SECRET:
        return False, "context contains secret data"
    if labels.pii:
        return False, "context contains PII"
    if ceiling is not None and labels.sensitivity > ceiling:
        return False, (f"context is {labels.sensitivity.name.lower()} but provider ceiling is "
                       f"{ceiling.name.lower()}")
    return True, "eligible"


@dataclass(frozen=True)
class RateLimits:
    """Any field left None is unlimited. Defaults to no limits."""
    rpm: Optional[int] = None
    rpd: Optional[int] = None
    tpm: Optional[int] = None
    tpd: Optional[int] = None


class RateLimiter:
    """Sliding-window limiter (60 s and 24 h windows). Clock is injectable for tests.

    `acquire` checks AND records in one step (the request is counted even if it later fails, since the
    provider counts it too). Returns (ok, reason, wait_seconds)."""

    def __init__(self, limits: RateLimits, clock: Callable[[], float] = time.monotonic):
        self.limits, self.clock = limits, clock
        self._events: Deque[Tuple[float, int]] = deque()      # (time, tokens)
        self._lock = threading.Lock()

    def _window(self, now: float, span: float) -> Tuple[int, int, float]:
        n = tok = 0
        oldest = now
        for t, k in self._events:
            if now - t < span:
                n += 1
                tok += k
                oldest = min(oldest, t)
        return n, tok, oldest

    def acquire(self, tokens: int) -> Tuple[bool, str, float]:
        with self._lock:
            now = self.clock()
            while self._events and now - self._events[0][0] >= 86400:
                self._events.popleft()
            L = self.limits
            n_m, tok_m, old_m = self._window(now, 60.0)
            n_d, tok_d, old_d = self._window(now, 86400.0)
            checks = [("rpm", L.rpm, n_m + 1, old_m, 60.0), ("tpm", L.tpm, tok_m + tokens, old_m, 60.0),
                      ("rpd", L.rpd, n_d + 1, old_d, 86400.0), ("tpd", L.tpd, tok_d + tokens, old_d, 86400.0)]
            for name, lim, would, oldest, span in checks:
                if lim is not None and would > lim:
                    return False, f"{name} limit {lim} reached", max(0.0, span - (now - oldest))
            self._events.append((now, tokens))
            return True, "", 0.0

    def correct(self, estimated: int, actual: int) -> None:
        """Replace the estimate recorded for the latest event with the provider-reported figure."""
        with self._lock:
            if self._events:
                t, k = self._events[-1]
                if k == estimated:
                    self._events[-1] = (t, actual)


@dataclass
class CloudEntry:
    """A cloud provider plus the policy that governs it."""
    provider: LLMProvider
    max_sensitivity: Sensitivity = Sensitivity.INTERNAL
    input_price_per_mtok: float = 0.0
    output_price_per_mtok: float = 0.0
    max_output_tokens: int = 0              # worst-case output reserved against the spend cap
    limits: RateLimits = field(default_factory=RateLimits)
    free: bool = False
    clock: Callable[[], float] = time.monotonic
    limiter: Optional[RateLimiter] = None

    def __post_init__(self):
        if self.limiter is None:
            self.limiter = RateLimiter(self.limits, self.clock)
        if self.free and (self.input_price_per_mtok or self.output_price_per_mtok):
            raise ValueError("a free entry must have zero prices")

    @property
    def name(self) -> str:
        return self.provider.name

    def worst_case_cost(self, in_tokens: int) -> float:
        return (in_tokens * self.input_price_per_mtok + self.max_output_tokens * self.output_price_per_mtok) / 1e6

    def actual_cost(self, in_tokens: int, out_tokens: int) -> float:
        return (in_tokens * self.input_price_per_mtok + out_tokens * self.output_price_per_mtok) / 1e6


class SpendLedger:
    """Daily (UTC) cloud-spend accounting with atomic reserve-before-call (BEGIN IMMEDIATE)."""

    def __init__(self, db_path: str, daily_cap_usd: float):
        self.db_path = db_path
        self.cap = daily_cap_usd
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS spend (day TEXT PRIMARY KEY, total REAL NOT NULL)")

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        return _Closing(c)

    @staticmethod
    def _today() -> str:
        return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")

    def reserve(self, amount: float) -> float:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute("SELECT total FROM spend WHERE day=?", (self._today(),)).fetchone()
                total = row[0] if row else 0.0
                if total + amount > self.cap:
                    c.execute("ROLLBACK")
                    raise SpendCapExceeded(
                        f"daily cloud-spend cap {self.cap:.2f} USD would be exceeded "
                        f"(spent/reserved {total:.4f} + {amount:.4f})")
                c.execute("INSERT INTO spend(day,total) VALUES(?,?) "
                          "ON CONFLICT(day) DO UPDATE SET total=total+excluded.total",
                          (self._today(), amount))
                c.execute("COMMIT")
            except SpendCapExceeded:
                raise
            except Exception:
                c.execute("ROLLBACK")
                raise
        return amount

    def adjust(self, delta: float) -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE spend SET total=MAX(0,total+?) WHERE day=?", (delta, self._today()))
            c.execute("COMMIT")

    def total_today(self) -> float:
        with self._conn() as c:
            row = c.execute("SELECT total FROM spend WHERE day=?", (self._today(),)).fetchone()
            return row[0] if row else 0.0


class _Closing:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        self.conn.close()
        return False


class PolicyRouter:
    """`cloud` items may be plain providers (legacy: INTERNAL ceiling, blended price, no limits) or CloudEntry."""

    def __init__(self, local: List[LLMProvider], cloud: List[Union[LLMProvider, CloudEntry]],
                 spend: Optional[SpendLedger] = None, price_per_1k_tokens_usd: float = 0.01):
        self.local = list(local)
        self.spend = spend
        self.price = price_per_1k_tokens_usd
        blended = price_per_1k_tokens_usd * 1000.0
        self.cloud: List[CloudEntry] = [
            c if isinstance(c, CloudEntry) else
            CloudEntry(c, Sensitivity.INTERNAL, blended, blended, max_output_tokens=0)
            for c in cloud]

    @staticmethod
    def _usage(r) -> Tuple[Optional[int], Optional[int]]:
        i, o = getattr(r, "input_tokens", None), getattr(r, "output_tokens", None)
        return (i if isinstance(i, int) else None, o if isinstance(o, int) else None)

    def _try_local(self, system_prompt, messages, attempts) -> Optional[ModelReply]:
        for p in self.local:
            if not p.is_configured():
                attempts.append({"provider": p.name, "tier": "local", "ok": False, "error": "not configured"})
                continue
            try:
                r = p.generate(system_prompt, messages)
                attempts.append({"provider": p.name, "tier": "local", "ok": True, "error": ""})
                i, o = self._usage(r)
                return ModelReply(r.text, r.provider, r.model, "local", attempts, i, o, 0.0)
            except LLMBadOutput as e:
                attempts.append({"provider": p.name, "tier": "local", "ok": True, "error": f"bad_output: {e}"[:200]})
                return ModelReply(e.salvage, p.name, getattr(p, "model", "?"), "local", attempts)
            except LLMError as e:
                attempts.append({"provider": p.name, "tier": "local", "ok": False, "error": str(e)})
        return None

    def generate(self, system_prompt: str, messages: List[Dict[str, str]], labels: Labels,
                 policy: ModelPolicy, est_tokens: int) -> ModelReply:
        attempts: List[dict] = []
        reply = self._try_local(system_prompt, messages, attempts)
        if reply:
            return reply
        ok, why = cloud_eligibility(labels, policy)
        if not ok:
            raise ModelUnavailable(f"local model unavailable and cloud is not permitted: {why}", attempts)
        if not any(e.provider.is_configured() for e in self.cloud):
            raise ModelUnavailable("local model unavailable and no cloud provider is configured", attempts)

        cap_blocked = False
        for entry in self.cloud:
            name = entry.name
            if not entry.provider.is_configured():
                attempts.append({"provider": name, "tier": "cloud", "ok": False, "error": "not configured"})
                continue
            ok, why = cloud_eligibility(labels, policy, entry.max_sensitivity)
            if not ok:
                attempts.append({"provider": name, "tier": "cloud", "ok": False, "error": f"skipped: {why}",
                                 "skipped": "ceiling"})
                continue
            allowed, lim_why, wait = entry.limiter.acquire(est_tokens + entry.max_output_tokens)
            if not allowed:
                attempts.append({"provider": name, "tier": "cloud", "ok": False, "error": f"skipped: {lim_why}",
                                 "skipped": "rate_limit", "wait_s": round(wait, 1)})
                continue
            reserved = 0.0
            if self.spend is not None and not entry.free:
                try:
                    reserved = self.spend.reserve(entry.worst_case_cost(est_tokens))
                except SpendCapExceeded as e:
                    cap_blocked = True
                    attempts.append({"provider": name, "tier": "cloud", "ok": False, "error": f"skipped: {e}",
                                     "skipped": "spend_cap"})
                    continue
            try:
                r = entry.provider.generate(system_prompt, messages)
            except LLMBadOutput as e:
                # The call happened (and may be billed): keep the reservation, hand the salvage text back so the
                # runner counts an invalid turn and repairs, instead of treating a model fault as an outage.
                attempts.append({"provider": name, "tier": "cloud", "ok": True, "error": f"bad_output: {e}"[:200]})
                return ModelReply(e.salvage, name, getattr(entry.provider, "model", "?"), "cloud", attempts,
                                  None, None, reserved)
            except LLMError as e:
                if reserved and self.spend is not None:
                    self.spend.adjust(-reserved)        # nothing was charged: refund the reservation
                attempts.append({"provider": name, "tier": "cloud", "ok": False, "error": str(e)})
                continue
            i, o = self._usage(r)
            cost = 0.0
            if not entry.free:
                if i is not None and o is not None:
                    cost = entry.actual_cost(i, o)
                    if self.spend is not None:
                        self.spend.adjust(cost - reserved)      # settle to the real figure
                else:
                    cost = reserved                             # no usage reported: keep the worst case
            if i is not None and o is not None:
                entry.limiter.correct(est_tokens + entry.max_output_tokens, i + o)
            attempts.append({"provider": name, "tier": "cloud", "ok": True, "error": ""})
            return ModelReply(r.text, r.provider, r.model, "cloud", attempts, i, o, cost)

        if cap_blocked and not any(a["ok"] for a in attempts) and \
                all(a.get("skipped") in ("spend_cap", "ceiling", "rate_limit") or a["error"] == "not configured"
                    for a in attempts if a["tier"] == "cloud"):
            raise SpendCapExceeded("daily cloud-spend cap blocks every eligible cloud provider")
        cloud_attempts = [a for a in attempts if a["tier"] == "cloud"]
        if cloud_attempts and all(a.get("skipped") == "ceiling" or a["error"] == "not configured"
                                  for a in cloud_attempts) and any(a.get("skipped") == "ceiling" for a in cloud_attempts):
            raise ModelUnavailable("blocked_by_policy: no cloud provider is permitted for this data's sensitivity", attempts)
        raise ModelUnavailable("local and cloud providers all failed", attempts)
