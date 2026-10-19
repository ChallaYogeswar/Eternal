"""Delegated capability tokens (plan section 6.1, amendments A2/A3).

A run gets ONE short-lived token, signed with Ed25519, that says exactly what that agent may do in that run:
which tools, up to which tier, how many calls, until when, for which human session and which origin channel.
The signing (private) key lives with the composition root that wraps the real chain; the verifier holds only the
PUBLIC key, so even code that can verify cannot mint. Agents, the broker and the runtime never see a key: the broker
carries an opaque token string and the model never sees it at all.

This module is pure policy + crypto: it imports no phase package and no network code, so the tripwire tests that
keep `orchestration/` away from the real chain keep holding. The thing that wraps the chain and calls
`CapabilityVerifier.verify` lives outside this package.

What the verifier adds is the question the existing rings do not ask: "is THIS agent, in THIS run, allowed THIS tool
at all?". It does not replace any existing decision; the QAP-P session state, trust, ROTATE, INSPECT, CONTAIN and
ATTRIBUTE rings still make theirs afterwards.

Fail-closed choices worth knowing:
  * a freshly started verifier refuses every token issued before it started (in-memory call counters would
    otherwise reset on restart and hand out fresh budgets);
  * revoking a run also revokes every token delegated from it;
  * every denial has a short stable reason string for the ledger and the tests.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, Optional, Protocol, Set, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

MAX_TOKEN_CHARS = 4096
MAX_TTL_S = 3600.0
VALID_ORIGINS = ("typed", "voice", "api")


class CapabilityError(ValueError):
    """Raised when a token cannot be issued (bad input, or an attenuation that would widen authority)."""


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def generate_keypair() -> Tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


def public_key_bytes(pub: Ed25519PublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def load_public_key(raw: bytes) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(raw)


@dataclass(frozen=True)
class CapabilityClaims:
    jti: str
    agent: str
    run_id: str
    root_run_id: str                    # the run this authority ultimately descends from (== run_id for a root)
    session_id: str
    tools: FrozenSet[str]
    max_tier: int
    call_cap: int
    iat: float
    exp: float
    origin_channel: str = "typed"
    parent_jti: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"jti": self.jti, "agent": self.agent, "run_id": self.run_id, "root_run_id": self.root_run_id,
                "session_id": self.session_id, "tools": sorted(self.tools), "max_tier": self.max_tier,
                "call_cap": self.call_cap, "iat": self.iat, "exp": self.exp, "origin_channel": self.origin_channel,
                "parent_jti": self.parent_jti}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "CapabilityClaims":
        try:
            tools = d["tools"]
            if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
                raise ValueError("tools")
            c = CapabilityClaims(
                jti=str(d["jti"]), agent=str(d["agent"]), run_id=str(d["run_id"]), root_run_id=str(d["root_run_id"]),
                session_id=str(d["session_id"]), tools=frozenset(tools), max_tier=int(d["max_tier"]),
                call_cap=int(d["call_cap"]), iat=float(d["iat"]), exp=float(d["exp"]),
                origin_channel=str(d["origin_channel"]), parent_jti=(None if d.get("parent_jti") is None else str(d["parent_jti"])))
        except (KeyError, TypeError, ValueError) as e:
            raise CapabilityError(f"malformed claims: {e}") from e
        if c.origin_channel not in VALID_ORIGINS or c.call_cap < 0 or c.max_tier < 0 or not c.jti:
            raise CapabilityError("malformed claims: out-of-range value")
        return c


def _encode(claims: CapabilityClaims, key: Ed25519PrivateKey) -> str:
    body = canonical(claims.to_dict())
    return f"{_b64(body)}.{_b64(key.sign(body))}"


def _decode(token: str, pub: Ed25519PublicKey) -> CapabilityClaims:
    """Signature-checked decode. Raises CapabilityError('malformed'|'bad_signature')."""
    if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS or token.count(".") != 1:
        raise CapabilityError("malformed")
    try:
        body_b64, sig_b64 = token.split(".")
        body, sig = _unb64(body_b64), _unb64(sig_b64)
    except Exception as e:  # noqa: BLE001
        raise CapabilityError("malformed") from e
    try:
        pub.verify(sig, body)
    except InvalidSignature as e:
        raise CapabilityError("bad_signature") from e
    try:
        return CapabilityClaims.from_dict(json.loads(body))
    except (ValueError, CapabilityError) as e:
        raise CapabilityError("malformed") from e


class CapabilityIssuer:
    """Mints tokens. Holds the private key; lives only in the composition root."""

    def __init__(self, private_key: Ed25519PrivateKey, *, clock: Callable[[], float] = time.time):
        self._key = private_key
        self._pub = private_key.public_key()
        self.clock = clock

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._pub

    def issue(self, *, agent: str, run_id: str, session_id: str, spec_tools: Iterable[str], spec_max_tier: int,
              call_cap: int, ttl_s: float, plan_tools: Optional[Iterable[str]] = None,
              plan_max_tier: Optional[int] = None, origin_channel: str = "typed") -> str:
        """Attenuated at issue time (I14): tools = spec allowlist INTERSECT plan subset; tier = the lower ceiling."""
        if origin_channel not in VALID_ORIGINS:
            raise CapabilityError(f"origin_channel must be one of {VALID_ORIGINS}")
        if not (0 < ttl_s <= MAX_TTL_S):
            raise CapabilityError(f"ttl_s must be in (0, {MAX_TTL_S}]")
        if call_cap < 0:
            raise CapabilityError("call_cap must be >= 0")
        if not (agent and run_id and session_id):
            raise CapabilityError("agent, run_id and session_id are required")
        tools = frozenset(spec_tools)
        if plan_tools is not None:
            tools &= frozenset(plan_tools)
        tier = spec_max_tier if plan_max_tier is None else min(spec_max_tier, plan_max_tier)
        now = self.clock()
        claims = CapabilityClaims(jti=uuid.uuid4().hex, agent=agent, run_id=run_id, root_run_id=run_id,
                                  session_id=session_id, tools=tools, max_tier=tier, call_cap=call_cap, iat=now,
                                  exp=now + ttl_s, origin_channel=origin_channel)
        return _encode(claims, self._key)

    def attenuate(self, parent_token: str, *, agent: str, run_id: str, tools: Iterable[str], call_cap: int,
                  ttl_s: float, max_tier: Optional[int] = None) -> str:
        """Delegation (Supervisor -> specialist): a child may only NARROW. Same session and origin, tools a subset,
        tier and call cap no higher, expiry no later. Revoking the parent's root run revokes the child."""
        parent = _decode(parent_token, self._pub)
        now = self.clock()
        if now >= parent.exp:
            raise CapabilityError("parent token expired")
        want = frozenset(tools)
        if not want <= parent.tools:
            raise CapabilityError(f"child would gain tools: {sorted(want - parent.tools)}")
        tier = parent.max_tier if max_tier is None else max_tier
        if tier > parent.max_tier:
            raise CapabilityError("child would gain tier")
        if call_cap > parent.call_cap:
            raise CapabilityError("child would gain calls")
        if not (0 < ttl_s):
            raise CapabilityError("ttl_s must be positive")
        claims = CapabilityClaims(jti=uuid.uuid4().hex, agent=agent, run_id=run_id, root_run_id=parent.root_run_id,
                                  session_id=parent.session_id, tools=want, max_tier=tier, call_cap=call_cap, iat=now,
                                  exp=min(parent.exp, now + ttl_s), origin_channel=parent.origin_channel,
                                  parent_jti=parent.jti)
        return _encode(claims, self._key)


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    reason: str
    claims: Optional[CapabilityClaims] = None


class CapabilityVerifier:
    """Public-key side. Stateful only for revocation and per-token call counts (in memory, by design: see module
    docstring on restart behaviour)."""

    def __init__(self, public_key: Ed25519PublicKey, *, tier_lookup: Callable[[str], Optional[int]],
                 clock: Callable[[], float] = time.time):
        self._pub = public_key
        self._tier_of = tier_lookup
        self.clock = clock
        self._started = clock()
        self._lock = threading.Lock()
        self._used: Dict[str, int] = {}
        self._revoked_jti: Set[str] = set()
        self._revoked_run: Set[str] = set()
        self._epoch = self._started          # tokens issued before this instant are void (kill switch / restart)

    # -- revocation ------------------------------------------------------------------------------
    def revoke_run(self, run_id: str) -> None:
        with self._lock:
            self._revoked_run.add(run_id)

    def revoke_token(self, jti: str) -> None:
        with self._lock:
            self._revoked_jti.add(jti)

    def revoke_all(self) -> None:
        """Kill switch: every token issued up to now stops working."""
        with self._lock:
            self._epoch = max(self._epoch, self.clock())
            self._used.clear()

    def calls_used(self, jti: str) -> int:
        with self._lock:
            return self._used.get(jti, 0)

    # -- the check -------------------------------------------------------------------------------
    def verify(self, token: Optional[str], *, session_id: str, tool: str, source_label: str, caller_kind: str,
               confirmation_channel: str) -> VerifyResult:
        if not token:
            return VerifyResult(False, "no_token")
        try:
            c = _decode(token, self._pub)
        except CapabilityError as e:
            return VerifyResult(False, str(e))
        now = self.clock()
        with self._lock:
            if now >= c.exp:
                return VerifyResult(False, "expired", c)
            if (c.iat < self._epoch or c.jti in self._revoked_jti or c.run_id in self._revoked_run
                    or c.root_run_id in self._revoked_run or (c.parent_jti and c.parent_jti in self._revoked_jti)):
                return VerifyResult(False, "revoked", c)
            if session_id != c.session_id:
                return VerifyResult(False, "wrong_session", c)
            if source_label != f"agent:{c.agent}":
                return VerifyResult(False, "wrong_identity", c)
            if caller_kind != "automated_routine":
                return VerifyResult(False, "caller_not_automated", c)
            if confirmation_channel != ("voice" if c.origin_channel == "voice" else "automated"):
                return VerifyResult(False, "channel_mismatch", c)
            if tool not in c.tools:
                return VerifyResult(False, "tool_out_of_scope", c)
            tier = self._tier_of(tool)
            if tier is None:
                return VerifyResult(False, "unknown_tool", c)
            if tier > c.max_tier:
                return VerifyResult(False, "tier_exceeded", c)
            used = self._used.get(c.jti, 0)
            if used >= c.call_cap:
                return VerifyResult(False, "call_cap_exhausted", c)
            self._used[c.jti] = used + 1
        return VerifyResult(True, "ok", c)


# --------------------------------------------------------------------------------- runner-facing port
class CapabilityHandle(Protocol):
    """What the runner holds for one run. `token` and `rotate_token` are for the broker only; `summary` is safe
    to ledger (it never contains the token)."""
    token: str

    def rotate_token(self, tool: str) -> Optional[str]: ...
    def summary(self) -> Dict[str, Any]: ...
    def close(self) -> None: ...


class CapabilityPort(Protocol):
    def begin(self, *, spec: Any, task: Any, session_id: str, origin_channel: str) -> CapabilityHandle:
        """Issue authority for one run. Raise to refuse (the run then fails closed without calling any tool)."""
