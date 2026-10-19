"""One-time, human-confirmed approvals for SENSITIVE actions (audit E-01 "bind approvals to a one-time challenge").

Flow:
  1. the caller asks for an approval naming session, tool and exact arguments  -> `request` returns an id;
  2. a DIFFERENT principal on a human channel (the owner's phone) reads `pending()` and `confirm`s it;
  3. the original caller presents the id with the call -> `consume` checks it is confirmed, unexpired, unused, for the
     same requester, session, tool and arguments, and burns it.

So changing JSON fields cannot create an approval, an approval cannot be reused or moved to other arguments, and the
party that wants the action cannot also be the party that confirms it.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .principals import HUMAN_CHANNELS, Principal

MAX_TTL_S = 600.0


class ApprovalError(Exception):
    pass


def args_digest(arguments: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass
class Approval:
    id: str
    requester: str
    session_id: Optional[str]
    tool_name: str
    digest: str
    summary: str
    expires_at: float
    confirmed_by: Optional[str] = None
    used: bool = False
    denied: bool = False


class ApprovalStore:
    def __init__(self, clock: Callable[[], float] = time.time, max_pending: int = 100):
        self.clock, self.max_pending = clock, max_pending
        self._lock = threading.Lock()
        self._items: Dict[str, Approval] = {}

    def _gc(self) -> None:
        now = self.clock()
        for k in [k for k, a in self._items.items() if now > a.expires_at + 60 or a.used]:
            del self._items[k]

    def request(self, requester: Principal, *, session_id: Optional[str], tool_name: str, arguments: Dict[str, Any],
                ttl_s: float = 120.0) -> Approval:
        if not (0 < ttl_s <= MAX_TTL_S):
            raise ApprovalError(f"ttl_s must be in (0, {MAX_TTL_S}]")
        with self._lock:
            self._gc()
            if sum(1 for a in self._items.values() if a.confirmed_by is None and not a.denied) >= self.max_pending:
                raise ApprovalError("too many pending approvals")
            a = Approval(secrets.token_urlsafe(16), requester.id, session_id, tool_name, args_digest(arguments),
                         f"{tool_name} {json.dumps(arguments, sort_keys=True, default=str)[:300]}",
                         self.clock() + ttl_s)
            self._items[a.id] = a
            return a

    def pending(self) -> List[Dict[str, Any]]:
        """What a human is shown before confirming: who wants what, exactly."""
        with self._lock:
            self._gc()
            now = self.clock()
            return [{"id": a.id, "requester": a.requester, "session_id": a.session_id, "tool_name": a.tool_name,
                     "summary": a.summary, "expires_in_s": round(a.expires_at - now, 1)}
                    for a in self._items.values() if a.confirmed_by is None and not a.denied and now <= a.expires_at]

    def confirm(self, approval_id: str, approver: Principal, *, approve: bool = True) -> Approval:
        if not (approver.channels & HUMAN_CHANNELS) or "approvals:confirm" not in approver.scopes:
            raise ApprovalError("only a principal on a human channel (app tap / typed) can confirm an approval")
        with self._lock:
            a = self._items.get(approval_id)
            if a is None or self.clock() > a.expires_at or a.used:
                raise ApprovalError("unknown, expired or already used approval")
            if a.requester == approver.id:
                raise ApprovalError("the requester cannot confirm its own approval")
            if not approve:
                a.denied = True
                return a
            a.confirmed_by = approver.id
            return a

    def consume(self, approval_id: Optional[str], caller: Principal, *, session_id: Optional[str], tool_name: str,
                arguments: Dict[str, Any]) -> Tuple[bool, str]:
        if not approval_id:
            return False, "SENSITIVE action requires a human-confirmed approval_id"
        with self._lock:
            a = self._items.get(approval_id)
            if a is None or self.clock() > a.expires_at:
                return False, "approval unknown or expired"
            if a.used:
                return False, "approval already used"
            if a.denied:
                return False, "approval was denied"
            if a.confirmed_by is None:
                return False, "approval not yet confirmed by a human"
            if a.requester != caller.id:
                return False, "approval belongs to a different caller"
            if a.session_id != session_id or a.tool_name != tool_name or a.digest != args_digest(arguments):
                return False, "approval was issued for a different session, tool or arguments"
            a.used = True
            return True, f"approved by {a.confirmed_by}"
