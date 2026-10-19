"""Per-run consent grants for unattended WRITE calls (plan section 6.5).

Without a grant a WRITE tool call makes the run PAUSE (`awaiting_confirmation`); it is not failed and it is never
blanket-approved. A grant names one run and one tool, a maximum number of uses and an expiry, and must be approved
through a non-voice, non-automated channel (typed text or a tap in the app): an agent cannot approve itself, and a
spoken request cannot approve its own downstream writes.

`check` is called by the broker right before dispatch and CONSUMES one use when it returns 'granted' (a call that
the chain then denies still used its grant: conservative on purpose).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

APPROVAL_CHANNELS = frozenset({"text_typed", "app_tap"})      # mirrors ConfirmationChannel minus voice/automated


class ConsentError(ValueError):
    pass


@dataclass
class ConsentGrant:
    run_id: str
    tool: str
    max_calls: int
    expires_at: float
    approved_via: str
    used: int = 0


class ConsentStore:
    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._grants: Dict[Tuple[str, str], ConsentGrant] = {}
        self._denied: set = set()
        self._pending: Dict[Tuple[str, str], float] = {}

    def approve(self, run_id: str, tool: str, *, via: str, max_calls: int = 1, ttl_s: float = 300.0) -> ConsentGrant:
        if via not in APPROVAL_CHANNELS:
            raise ConsentError(f"approval must come through one of {sorted(APPROVAL_CHANNELS)}, not '{via}'")
        if not (1 <= max_calls <= 100) or not (0 < ttl_s <= 3600):
            raise ConsentError("max_calls must be 1..100 and ttl_s must be in (0, 3600]")
        g = ConsentGrant(run_id, tool, max_calls, self.clock() + ttl_s, via)
        with self._lock:
            self._grants[(run_id, tool)] = g
            self._denied.discard((run_id, tool))
            self._pending.pop((run_id, tool), None)
        return g

    def deny(self, run_id: str, tool: str) -> None:
        with self._lock:
            self._denied.add((run_id, tool))
            self._grants.pop((run_id, tool), None)
            self._pending.pop((run_id, tool), None)

    def revoke_run(self, run_id: str) -> None:
        """Grants expire with the run."""
        with self._lock:
            for k in [k for k in self._grants if k[0] == run_id]:
                del self._grants[k]
            for k in [k for k in self._pending if k[0] == run_id]:
                del self._pending[k]

    def check(self, tool_name: str, run_id: str) -> str:
        key = (run_id, tool_name)
        with self._lock:
            if key in self._denied:
                return "denied"
            g = self._grants.get(key)
            if g is not None and self.clock() < g.expires_at and g.used < g.max_calls:
                g.used += 1
                return "granted"
            self._pending[key] = self.clock()
            return "pending"

    def pending(self) -> List[Tuple[str, str]]:
        """What a human is being asked to approve (run_id, tool), oldest first."""
        with self._lock:
            return [k for k, _ in sorted(self._pending.items(), key=lambda kv: kv[1])]
