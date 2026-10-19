"""Signed identity envelope for MCP servers (amendment A4).

The stateless MCP executor forwards only a tool name and arguments, and the agent writes its own arguments, so a
server cannot learn WHO is calling from the arguments alone. The capability layer (outside this package, after the
verifier approved the call) adds one reserved argument, `_identity`, signed with the signing key the model and the
broker never see. A server that needs to know the caller (`tasks`, `drafts`) verifies it with the PUBLIC key from its
environment and rejects the call otherwise.

The signature covers the identity, run, tool, an expiry and a hash of the other arguments, so an envelope cannot be
lifted onto a different tool or different arguments. Within its short lifetime an identical call can be replayed;
that is harmless for the narrow, idempotent-by-design tools it protects and is why `exp` is seconds, not minutes.

Dependency-light on purpose (stdlib + cryptography): a server may import this module or vendor it.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Callable, Dict, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .capability import _b64, _unb64, canonical

RESERVED_KEY = "_identity"
DEFAULT_TTL_S = 30.0


def args_digest(arguments: Dict[str, Any]) -> str:
    clean = {k: v for k, v in arguments.items() if k != RESERVED_KEY}
    return hashlib.sha256(canonical(clean)).hexdigest()


def sign_envelope(key: Ed25519PrivateKey, *, identity: str, run_id: str, tool: str, arguments: Dict[str, Any],
                  ttl_s: float = DEFAULT_TTL_S, clock: Callable[[], float] = time.time) -> Dict[str, Any]:
    """`identity` is 'agent:<name>' or 'human:<id>'. Returns the dict to place under `_identity`."""
    body = {"identity": identity, "run_id": run_id, "tool": tool, "args": args_digest(arguments),
            "exp": round(clock() + ttl_s, 3)}
    return {**body, "sig": _b64(key.sign(canonical(body)))}


def verify_envelope(pub: Ed25519PublicKey, arguments: Dict[str, Any], *, tool: str,
                    clock: Callable[[], float] = time.time) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Server side. Returns (ok, reason, envelope-fields). The arguments passed in must be exactly what the server
    received (including `_identity`); the digest is computed over everything else."""
    env = arguments.get(RESERVED_KEY)
    if not isinstance(env, dict):
        return False, "missing_identity", None
    try:
        body = {k: env[k] for k in ("identity", "run_id", "tool", "args", "exp")}
        sig = _unb64(env["sig"])
        pub.verify(sig, canonical(body))
    except (KeyError, TypeError, ValueError, InvalidSignature):
        return False, "bad_identity_signature", None
    if body["tool"] != tool:
        return False, "identity_for_other_tool", None
    if clock() >= float(body["exp"]):
        return False, "identity_expired", None
    if body["args"] != args_digest(arguments):
        return False, "identity_for_other_arguments", None
    return True, "ok", body
