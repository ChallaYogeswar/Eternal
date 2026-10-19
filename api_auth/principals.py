"""Principals: who is calling, derived from a bearer token the server recognises.

Tokens are random secrets; the server stores only their SHA-256 (a high-entropy token needs no slow hash). The
configuration file (path in `ETERNAL_AUTH_FILE`) looks like:

    {"principals": [
        {"id": "phone",  "role": "owner_app",    "token_sha256": "<hex>"},
        {"id": "mic",    "role": "voice_client", "token_sha256": "<hex>"},
        {"id": "cron",   "role": "automation",   "token_sha256": "<hex>"}]}

Roles fix the channels, caller kind and scopes. A caller cannot widen them by what it puts in a request.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, FrozenSet, List, Optional

HUMAN_CHANNELS = frozenset({"app_tap", "text_typed"})

ALL_SCOPES = frozenset({
    "tools:call", "tools:grant", "consent:write", "consent:read", "approvals:request", "approvals:confirm",
    "contain:decide", "kill:trigger", "memory:write", "memory:read", "voice:use", "ledger:read",
    "qap:read", "qap:write", "qap:admin", "trading:read", "trading:write",
})


@dataclass(frozen=True)
class RoleSpec:
    channels: FrozenSet[str]
    caller_kind: str
    scopes: FrozenSet[str]


ROLES: Dict[str, RoleSpec] = {
    # The owner's phone / terminal: a human can tap or type. The only role that can confirm approvals.
    "owner_app": RoleSpec(frozenset({"app_tap", "text_typed"}), "owner_interactive", ALL_SCOPES),
    # Microphone front-end: voice can call ordinary tools but never confirm, grant consent or touch admin routes.
    "voice_client": RoleSpec(frozenset({"voice"}), "owner_interactive", frozenset({
        "tools:call", "tools:grant", "approvals:request", "voice:use", "ledger:read", "qap:read", "consent:read",
        "memory:read"})),
    # Scheduled routines and agents: automated channel, automated caller kind, no human powers.
    "automation": RoleSpec(frozenset({"automated"}), "automated_routine", frozenset({
        "tools:call", "tools:grant", "approvals:request", "ledger:read", "qap:read", "consent:read", "memory:read"})),
    "readonly": RoleSpec(frozenset(), "owner_interactive", frozenset({
        "ledger:read", "qap:read", "consent:read", "memory:read", "trading:read"})),
}


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    channels: FrozenSet[str]
    caller_kind: str
    scopes: FrozenSet[str]

    @property
    def is_human(self) -> bool:
        return bool(self.channels & HUMAN_CHANNELS)

    @property
    def default_channel(self) -> Optional[str]:
        for c in ("app_tap", "text_typed", "voice", "automated"):
            if c in self.channels:
                return c
        return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return "etn_" + secrets.token_urlsafe(32)


class AuthRegistry:
    def __init__(self, entries: Optional[List[dict]] = None, *, max_failures: int = 10, window_s: float = 60.0,
                 clock=time.time):
        self._by_hash: Dict[str, Principal] = {}
        self.clock, self.max_failures, self.window_s = clock, max_failures, window_s
        self._fail: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        seen = set()
        for e in entries or []:
            role = e["role"]
            if role not in ROLES:
                raise ValueError(f"unknown role '{role}' for principal '{e.get('id')}'")
            if e["id"] in seen:
                raise ValueError(f"duplicate principal id '{e['id']}'")
            seen.add(e["id"])
            h = str(e["token_sha256"]).lower()
            if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
                raise ValueError(f"principal '{e['id']}' needs a 64-hex token_sha256")
            r = ROLES[role]
            self._by_hash[h] = Principal(e["id"], role, r.channels, r.caller_kind, r.scopes)

    @property
    def configured(self) -> bool:
        return bool(self._by_hash)

    @classmethod
    def from_file(cls, path: str, **kw) -> "AuthRegistry":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f)["principals"], **kw)

    @classmethod
    def from_env(cls) -> "AuthRegistry":
        path = os.environ.get("ETERNAL_AUTH_FILE")
        if not path or not os.path.exists(path):
            return cls([])                       # nothing configured -> every protected route refuses
        return cls.from_file(path)

    def throttled(self, client: str) -> bool:
        now = self.clock()
        with self._lock:
            q = self._fail[client]
            while q and now - q[0] > self.window_s:
                q.popleft()
            return len(q) >= self.max_failures

    def authenticate(self, token: Optional[str], client: str = "?") -> Optional[Principal]:
        """Constant-time over every configured principal; counts failures per client."""
        found: Optional[Principal] = None
        if token:
            h = hash_token(token)
            for known, p in self._by_hash.items():
                if hmac.compare_digest(known, h):
                    found = p
        if found is None:
            with self._lock:
                self._fail[client].append(self.clock())
        return found
