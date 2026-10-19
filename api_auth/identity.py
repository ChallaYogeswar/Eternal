"""Derive the call identity (channel + caller kind) from the authenticated principal, never from the request body."""
from __future__ import annotations

from typing import Optional, Tuple

from .principals import Principal


class IdentityRefused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def derive_call_identity(principal: Principal, *, requested_channel: Optional[str] = None,
                         requested_caller_kind: Optional[str] = None) -> Tuple[str, str]:
    """Returns (confirmation_channel, caller_kind). A body may NARROW or restate what the principal already is; it can
    never claim anything the principal does not hold. A mismatch is refused loudly rather than silently overridden, so
    a client trying to elevate itself shows up in logs and tests."""
    if principal.default_channel is None:
        raise IdentityRefused(f"principal '{principal.id}' (role {principal.role}) may not make tool calls")
    channel = requested_channel or principal.default_channel
    if channel not in principal.channels:
        raise IdentityRefused(
            f"principal '{principal.id}' may not use channel '{channel}' (allowed: {sorted(principal.channels)})")
    if requested_caller_kind is not None and requested_caller_kind != principal.caller_kind:
        raise IdentityRefused(
            f"principal '{principal.id}' is '{principal.caller_kind}', not '{requested_caller_kind}'")
    return channel, principal.caller_kind
