"""
Ring 1 — PERIMETER.

RCAP whitepaper Section 3.1: "Rate limiting and tarpit-based throttling of
unauthenticated traffic. No inspection beyond volumetric and connection-rate
signals." Eternal_Master_Build_Plan.md Section 6.2, personal-scale role:
"Rate-limit any externally-reachable surface (webhook, email-triggered
agent, web search)."

Personal-scale reading: there's no literal "unauthenticated traffic" inside
a one-user assistant process, but there IS a real analog — a tool whose
*execution* reaches outside the local machine (web search being the
Phase 3 example) can be hammered by a runaway agentic loop, a bug in the
graph's retry logic, or a prompt-injected instruction trying to exfiltrate
data via repeated calls. PERIMETER's job is exactly the same shape at any
scale: bound how often a given (source, tool) pair may fire, and — per the
whitepaper's explicit design note — throttle rather than silently succeed
or hard-fail, so a burst looks like backpressure, not a crash.

This is a sliding-window limiter, not a token bucket: simpler to reason
about and to test deterministically, and personal-scale call volumes don't
need a bucket's burst-then-refill nuance.
"""
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

from .config import Config


@dataclass
class PerimeterDecision:
    allowed: bool
    tarpitted_seconds: float
    calls_in_window: int
    limit: int
    reason: str = ""


class PerimeterLimiter:
    def __init__(self, config: Config):
        self.config = config
        # key: (source_label, tool_name) -> list of call timestamps
        self._history: Dict[Tuple[str, str], List[float]] = defaultdict(list)
        self._custom_limits: Dict[str, int] = {}

    def set_limit_for_tool(self, tool_name: str, limit_per_window: int) -> None:
        """Per-tool override — e.g. a MCP server manifest can declare a
        tighter limit than the config default (see registry.py,
        ToolManifestEntry.rate_limit_per_minute)."""
        self._custom_limits[tool_name] = limit_per_window

    def _limit_for(self, tool_name: str) -> int:
        return self._custom_limits.get(tool_name, self.config.perimeter_default_limit_per_window)

    def check(self, source_label: str, tool_name: str, now: float = None) -> PerimeterDecision:
        now = now if now is not None else time.time()
        key = (source_label, tool_name)
        window_start = now - self.config.perimeter_window_seconds

        history = self._history[key]
        # prune anything outside the sliding window
        history[:] = [t for t in history if t >= window_start]

        limit = self._limit_for(tool_name)
        if len(history) >= limit:
            # Over limit: tarpit rather than instant-reject. The *caller*
            # of check() is responsible for actually sleeping
            # tarpitted_seconds (kept as a separate, explicit field rather
            # than sleeping inside this method) so unit tests can assert on
            # the decision without paying the real wall-clock cost every
            # time — see tests/test_perimeter.py.
            return PerimeterDecision(
                allowed=False,
                tarpitted_seconds=self.config.perimeter_tarpit_seconds,
                calls_in_window=len(history),
                limit=limit,
                reason=f"rate limit exceeded: {len(history)}/{limit} calls in "
                       f"{self.config.perimeter_window_seconds:.0f}s window for "
                       f"tool '{tool_name}' from source '{source_label}'",
            )

        history.append(now)
        return PerimeterDecision(
            allowed=True, tarpitted_seconds=0.0, calls_in_window=len(history), limit=limit
        )

    def reset(self) -> None:
        """Test/demo helper — not part of the protocol, just avoids
        cross-test pollution of the sliding-window history."""
        self._history.clear()
