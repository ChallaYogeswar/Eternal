"""Shared provider plumbing: injectable HTTP, error mapping, usage-carrying result."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from eternal_core.llm.base import LLMAuthError, LLMResponseError, LLMResult, LLMUnavailableError

from ..llm_errors import LLMBadOutput


class LLMRateLimitError(LLMUnavailableError):
    """HTTP 429 / quota exhausted. Subclass of LLMUnavailableError so the router treats it as
    'try the next provider'; carries the server's retry hint when given."""
    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class UsageResult(LLMResult):
    """LLMResult plus provider-reported token usage (None when the API did not report it)."""
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


@dataclass
class HttpResponse:
    status: int
    text: str
    headers: Dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        import json
        return json.loads(self.text)


HttpPost = Callable[[str, Dict[str, str], Dict[str, Any], float], HttpResponse]


def default_http_post(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: float) -> HttpResponse:
    import httpx
    try:
        r = httpx.post(url, headers=headers, json=payload, timeout=timeout)
    except httpx.HTTPError as e:
        raise LLMUnavailableError(f"unreachable: {type(e).__name__}: {e}") from e
    return HttpResponse(r.status_code, r.text, {k.lower(): v for k, v in r.headers.items()})


def parse_retry_after(headers: Dict[str, str]) -> Optional[float]:
    v = headers.get("retry-after")
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def raise_for_status(name: str, resp: HttpResponse) -> None:
    if resp.status == 200:
        return
    snippet = resp.text[:300]
    if resp.status in (401, 403):
        raise LLMAuthError(f"{name} auth failed: HTTP {resp.status}: {snippet}")
    if resp.status == 429:
        raise LLMRateLimitError(f"{name} rate limited: {snippet}", parse_retry_after(resp.headers))
    if resp.status >= 500:
        raise LLMUnavailableError(f"{name} server error: HTTP {resp.status}: {snippet}")
    if resp.status == 400:
        salvage = _bad_output_text(resp.text)
        if salvage is not None:
            raise LLMBadOutput(f"{name} rejected the model's output: {snippet}", salvage)
    raise LLMResponseError(f"{name} returned HTTP {resp.status}: {snippet}")


_BAD_OUTPUT_MARKERS = ("tool_use_failed", "invalid json", "invalid_json", "generated invalid", "failed_generation",
                       "tool choice is none", "attempted to call tool", "malformed function call")


def _bad_output_text(body: str) -> Optional[str]:
    """If a 400 body says the MODEL produced something the provider could not accept (a native tool call with no
    tools declared, JSON it deemed invalid), return the best available text of what the model generated, else
    None (a genuine bad-request error must stay an error)."""
    low = body.lower()
    if not any(m in low for m in _BAD_OUTPUT_MARKERS):
        return None
    import json
    try:
        err = json.loads(body)
        err = err.get("error", err) if isinstance(err, dict) else {}
        failed = err.get("failed_generation") if isinstance(err, dict) else None
        msg = err.get("message") if isinstance(err, dict) else None
    except ValueError:
        failed, msg = None, None
    if isinstance(failed, str) and failed.strip():
        return failed
    return f"[provider rejected the model's output: {(msg or body)[:300]}]"


def timed(fn):
    start = time.time()
    out = fn()
    return out, time.time() - start
