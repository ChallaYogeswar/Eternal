"""CapabilityGateway: the one new door in front of the existing chain for agent traffic.

Order of events for an agent call:
  1. must be an AgentCallRequest (anything else is refused: agents never get the plain request path);
  2. the capability verifier checks token, run, session, identity, channel, tool, tier, call cap, revocation;
  3. reserved `_` arguments are stripped, then (only for tools that need to know the caller) a signed `_identity`
     envelope is attached (amendment A4);
  4. a PLAIN WriteCallRequest goes to the unchanged inner chain (Phase 6 -> 5 -> 4 -> RCAP). Those layers still make
     their own decisions; this layer only adds "is this agent, in this run, allowed this tool at all?".

Every denial is written to the chain ledger with run, agent, tool and reason, never the token.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, FrozenSet, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from orchestration.capability import CapabilityVerifier
from orchestration.identity import RESERVED_KEY, sign_envelope
from voice_consent_gateway.models import ConfirmationChannel, WriteCallRequest, WriteCallResult

from .request import AgentCallRequest


class CapabilityGateway:
    def __init__(self, inner: Any, verifier: CapabilityVerifier, *, ledger: Any,
                 identity_key: Optional[Ed25519PrivateKey] = None, identity_tools: FrozenSet[str] = frozenset(),
                 clock: Callable[[], float] = time.time):
        self.inner, self.verifier, self.ledger = inner, verifier, ledger
        self._identity_key, self.identity_tools, self.clock = identity_key, frozenset(identity_tools), clock
        if self.identity_tools and identity_key is None:
            raise ValueError("identity_tools requires an identity_key")

    def _deny(self, request: Any, reason: str, run_id: Optional[str] = None, agent: Optional[str] = None) -> WriteCallResult:
        h = self.ledger.append("agent_capability_denied", {
            "tool_name": getattr(request, "tool_name", None), "session_id": getattr(request, "session_id", None),
            "source_label": getattr(request, "source_label", None), "reason": reason, "run_id": run_id, "agent": agent,
        })
        return WriteCallResult(tool_name=getattr(request, "tool_name", "?"), ok=False, content=None,
                               denied_reason=f"capability: {reason}", underlying_result=None, ledger_entry_hashes=[h])

    async def call_tool(self, request: Any) -> WriteCallResult:
        if not isinstance(request, AgentCallRequest):
            return self._deny(request, "not_an_agent_call_request")
        chan = request.confirmation_channel
        v = self.verifier.verify(
            request.capability_token, session_id=request.session_id or "", tool=request.tool_name,
            source_label=request.source_label, caller_kind=request.caller_kind,
            confirmation_channel=getattr(chan, "value", chan))
        if not v.ok:
            return self._deny(request, v.reason, v.claims.run_id if v.claims else None,
                              v.claims.agent if v.claims else None)
        claims = v.claims
        args: Dict[str, Any] = {k: val for k, val in (request.arguments or {}).items() if not str(k).startswith("_")}
        if request.tool_name in self.identity_tools:
            args[RESERVED_KEY] = sign_envelope(self._identity_key, identity=f"agent:{claims.agent}",
                                               run_id=claims.run_id, tool=request.tool_name, arguments=args,
                                               clock=self.clock)
        plain = WriteCallRequest(
            tool_name=request.tool_name, arguments=args, session_id=request.session_id,
            confirmation_channel=request.confirmation_channel, source_label=request.source_label,
            explanation=request.explanation, rotate_token=request.rotate_token, caller_kind=request.caller_kind)
        return await self.inner.call_tool(plain)

    def grant_tool_permission(self, session_id: str, tool_name: str) -> str:
        return self.inner.grant_tool_permission(session_id, tool_name)

    def kill(self, **kwargs: Any) -> Any:
        """Kill switch. Agent authority is revoked FIRST (so nothing can slip a call in while the sessions are being
        quarantined), then the existing kill switch runs unchanged."""
        self.verifier.revoke_all()
        phase5 = getattr(self.inner, "phase5_gateway", self.inner)
        return phase5.pull_kill_switch(**kwargs)


class CapabilityCoreGateway:
    """Synchronous adapter with the same call shape as Phase6CoreGateway plus `capability_token`. This is the object
    the ToolBroker is given as its gateway."""

    def __init__(self, gateway: CapabilityGateway):
        self.gateway = gateway

    def call_tool(self, *, session_id: str, tool_name: str, arguments: Dict[str, Any], source_label: str,
                  explanation: str, confirmation_channel: str = "automated", caller_kind: str = "automated_routine",
                  rotate_token: Optional[str] = None, capability_token: Optional[str] = None) -> Dict[str, Any]:
        request = AgentCallRequest(
            tool_name=tool_name, arguments=arguments, session_id=session_id,
            confirmation_channel=ConfirmationChannel(confirmation_channel), source_label=source_label,
            explanation=explanation, rotate_token=rotate_token, caller_kind=caller_kind,
            capability_token=capability_token)
        result = asyncio.run(self.gateway.call_tool(request))
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return dict(result.__dict__)
