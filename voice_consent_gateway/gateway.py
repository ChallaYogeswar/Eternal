"""
Phase4Gateway — the composition root for Phase 4's write path.

Deliberately a thin WRAPPER around rcap_p.gateway.RCAPGateway, not a
reimplementation or a subclass reaching into its internals: every
PERIMETER/ROTATE/INSPECT/DECEIVE/CONTAIN/ATTRIBUTE/FASTLANE guarantee
Phase 3 already proved (77 unit tests, 37 narrative checks, 18 live MCP
round trips) applies unchanged to every ÆTHER write tool call that reaches
RCAPGateway.call_tool — Phase 4 adds two NEW gates in front of it, and
changes nothing about what happens once a call gets past them.

Call order, and why it's this order (mirrors the reasoning style
rcap_p.gateway's own docstring uses):

  1. VOICE GATE (voice_gate.py) — runs first, unconditionally, before
     consent and before RCAP-P is even consulted. Build plan Section 6.3
     point 2 says SENSITIVE-tier voice-triggering is refused "regardless
     of trust score" — if this ran after RCAP-P's FASTLANE check, a
     sufficiently trusted, hardware-attested session could reach FASTLANE
     and this rule would never even be evaluated for it. Running it first
     makes the "regardless of" clause literally true rather than true by
     convention.
  2. CONSENT (consent.py) — WRITE and SENSITIVE tools require an existing
     consent grant for that exact tool_name. This is Section 5.2's
     "fine-grained per-tool consent instead of all-or-nothing," and it is
     deliberately a SEPARATE, longer-lived grant from RCAP-P's ROTATE
     permission token (see consent.py's module docstring for why).
  3. RCAP-P (rcap_p.gateway.RCAPGateway.call_tool) — everything Phase 3
     already built and proved, completely unchanged.
  4. EXPLANATION LOGGING — after the call resolves (whichever gate
     stopped it, if any), one ledger entry is written carrying the
     caller-supplied `explanation` alongside the outcome. This is the
     literal mechanism behind the exit criterion's "is logged with an
     explanation" clause: the explanation is written in the caller's own
     words (voice/intent.py constructs it from the actual transcript),
     not reconstructed from the tool call after the fact.
"""
import time
from typing import Any, Dict, Optional

from rcap_p.models import CallerKind, ToolCallContext, ToolCallResult, ToolManifestEntry

from .consent import ConsentRegistry
from .ledger_bridge import Ledger
from .models import ConfirmationChannel, WriteCallRequest, WriteCallResult
from .voice_gate import check_voice_gate


class Phase4Gateway:
    def __init__(self, rcap_gateway, registry: Dict[str, ToolManifestEntry],
                 consent_registry: ConsentRegistry, ledger: Ledger):
        self.rcap_gateway = rcap_gateway
        self.registry = registry
        self.consent_registry = consent_registry
        self.ledger = ledger

    async def call_tool(self, request: WriteCallRequest) -> WriteCallResult:
        ledger_hashes = []
        manifest = self.registry.get(request.tool_name)

        if manifest is None:
            h = self.ledger.append("phase4_unknown_tool", {"tool_name": request.tool_name})
            return WriteCallResult(
                tool_name=request.tool_name, ok=False, content=None,
                denied_reason=f"unknown tool '{request.tool_name}'",
                underlying_result=None, ledger_entry_hashes=[h],
            )

        # --- Gate 1: voice gate, unconditional, runs before consent and
        # before RCAP-P is consulted at all. See module docstring. ---
        voice_decision = check_voice_gate(manifest, request.confirmation_channel)
        if not voice_decision.allowed:
            h = self.ledger.append("phase4_voice_gate_denied", {
                "tool_name": request.tool_name,
                "confirmation_channel": request.confirmation_channel.value,
                "session_id": request.session_id,
                "explanation": request.explanation,
                "reason": voice_decision.reason,
            })
            return WriteCallResult(
                tool_name=request.tool_name, ok=False, content=None,
                denied_reason=voice_decision.reason,
                underlying_result=None, ledger_entry_hashes=[h],
            )

        # --- Gate 2: per-tool consent, for WRITE and SENSITIVE tools ---
        from rcap_p.models import ToolSensitivity
        if manifest.sensitivity in (ToolSensitivity.WRITE, ToolSensitivity.SENSITIVE):
            if not self.consent_registry.has_consent(request.tool_name):
                h = self.ledger.append("phase4_consent_denied", {
                    "tool_name": request.tool_name,
                    "session_id": request.session_id,
                    "explanation": request.explanation,
                })
                return WriteCallResult(
                    tool_name=request.tool_name, ok=False, content=None,
                    denied_reason=(
                        f"'{request.tool_name}' requires consent before Eternal may call it "
                        "(build plan Section 5.2: per-tool consent). Grant it via "
                        "ConsentRegistry.grant(...) / POST /consent/grant, then retry."
                    ),
                    underlying_result=None, ledger_entry_hashes=[h],
                )

        # --- Gate 3: RCAP-P, unchanged from Phase 3 ---
        ctx = ToolCallContext(
            session_id=request.session_id,
            caller_kind=CallerKind(request.caller_kind),
            source_label=request.source_label,
        )
        underlying: ToolCallResult = await self.rcap_gateway.call_tool(
            ctx, request.tool_name, request.arguments, rotate_token=request.rotate_token,
        )
        ledger_hashes.extend(underlying.ledger_entry_hashes)

        # --- Gate 4 (not really a gate): explanation logging, always,
        # regardless of outcome. This is what makes "logged with an
        # explanation" true even for a call RCAP-P itself denied
        # (e.g. no ROTATE token yet) — the attempt and its stated reason
        # are still on the record. ---
        h = self.ledger.append("phase4_write_call_explained", {
            "tool_name": request.tool_name,
            "session_id": request.session_id,
            "confirmation_channel": request.confirmation_channel.value,
            "explanation": request.explanation,
            "ok": underlying.ok,
            "containment_state": underlying.containment_state.value,
        })
        ledger_hashes.append(h)

        return WriteCallResult(
            tool_name=request.tool_name, ok=underlying.ok, content=underlying.content,
            denied_reason=None if underlying.ok else underlying.error,
            underlying_result=underlying, ledger_entry_hashes=ledger_hashes,
        )
