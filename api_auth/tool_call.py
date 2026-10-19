"""One place that turns an authenticated HTTP tool call into (channel, caller kind) and enforces the SENSITIVE rule,
so the RCAP, Phase 4, Phase 5 and Phase 6 daemons cannot drift apart."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .approvals import ApprovalError, ApprovalStore
from .fastapi_deps import require
from .identity import IdentityRefused, derive_call_identity
from .principals import Principal


def is_sensitive(registry: Dict[str, Any], tool_name: str) -> bool:
    entry = registry.get(tool_name)
    if entry is None:
        return False
    s = getattr(entry.sensitivity, "value", entry.sensitivity)
    return s == "sensitive"


def authorize_tool_call(principal: Principal, *, approvals: ApprovalStore, registry: Dict[str, Any], tool_name: str,
                        session_id: Optional[str], arguments: Dict[str, Any], requested_channel: Optional[str],
                        requested_caller_kind: Optional[str], approval_id: Optional[str]) -> Tuple[str, str]:
    """Raises HTTPException(403) on any refusal; returns (confirmation_channel, caller_kind) otherwise.

    SENSITIVE tools additionally need a one-time approval a DIFFERENT human principal confirmed for exactly this
    session, tool and arguments; voice and automated callers are refused outright (voice may never authorise these)."""
    try:
        channel, caller_kind = derive_call_identity(
            principal, requested_channel=requested_channel, requested_caller_kind=requested_caller_kind)
    except IdentityRefused as e:
        raise HTTPException(403, e.reason)
    if is_sensitive(registry, tool_name):
        if channel not in ("app_tap", "text_typed"):
            raise HTTPException(403, f"'{tool_name}' is SENSITIVE and cannot be called over channel '{channel}'")
        ok, why = approvals.consume(approval_id, principal, session_id=session_id, tool_name=tool_name,
                                    arguments=arguments)
        if not ok:
            raise HTTPException(403, why)
    return channel, caller_kind


class ApprovalRequestBody(BaseModel):
    session_id: Optional[str] = None
    tool_name: str
    arguments: Dict[str, Any] = {}
    ttl_s: float = 120.0


class ApprovalDecisionBody(BaseModel):
    approve: bool = True


def build_approval_router(approvals: ApprovalStore, registry: Dict[str, Any]) -> APIRouter:
    r = APIRouter(prefix="/approvals", tags=["approvals"])

    @r.post("/request")
    def request_approval(body: ApprovalRequestBody, p: Principal = Depends(require("approvals:request"))):
        if body.tool_name not in registry:
            raise HTTPException(404, f"unknown tool '{body.tool_name}'")
        try:
            a = approvals.request(p, session_id=body.session_id, tool_name=body.tool_name, arguments=body.arguments,
                                  ttl_s=body.ttl_s)
        except ApprovalError as e:
            raise HTTPException(400, str(e))
        return {"approval_id": a.id, "expires_in_s": body.ttl_s,
                "next": "a different human principal must confirm it at POST /approvals/{id}/decision"}

    @r.get("/pending")
    def pending(p: Principal = Depends(require("approvals:confirm"))):
        return approvals.pending()

    @r.post("/{approval_id}/decision")
    def decide(approval_id: str, body: ApprovalDecisionBody, p: Principal = Depends(require("approvals:confirm"))):
        try:
            a = approvals.confirm(approval_id, p, approve=body.approve)
        except ApprovalError as e:
            raise HTTPException(403, str(e))
        return {"approval_id": a.id, "confirmed": a.confirmed_by is not None, "denied": a.denied}

    return r
