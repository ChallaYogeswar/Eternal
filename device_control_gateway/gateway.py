"""
Phase5Gateway — the composition root for Phase 5's remote-control write
path.

Deliberately a thin WRAPPER around voice_consent_gateway.gateway.Phase4Gateway, not a
reimplementation or a subclass reaching into its internals — same
discipline Phase4Gateway itself used relative to RCAPGateway. Every
VOICE-GATE / CONSENT / PERIMETER / ROTATE / INSPECT / DECEIVE / CONTAIN /
ATTRIBUTE / FASTLANE guarantee already proved in Phases 3 and 4 applies
unchanged to every Phase 5 tool call that reaches Phase4Gateway.call_tool.
Phase 5 adds exactly ONE new gate, in front of it, for exactly ONE tool.

Why only one tool gets the extra gate: build plan Section 7.1 names "take
over full control of my laptop" as "the single most sensitive action in
the whole system" — not every SENSITIVE-tier tool. laptop_unlock,
laptop_shutdown, ha_lock, and ha_unlock are SENSITIVE and get the
ordinary voice-gate + consent + RCAP-P treatment Phase 4 already proved
against a fixture; remote_control_request_full_session gets that SAME
treatment PLUS the top-trust-tier check, because handing over a real
keyboard/mouse/screen session is categorically more consequential than
any single lock/unlock/shutdown call.

Gate ordering for the one gated tool, and why it's this order:

  1. TOP-TRUST-TIER (trust_tier.py) — runs FIRST, before Phase4Gateway is
     even called, so a low-trust session is refused before its request
     ever reaches voice-gate/consent/RCAP-P at all, and — critically —
     before any underlying action (Wake-on-LAN, handoff construction)
     could execute. This has to run before delegating to Phase4Gateway,
     not after: running it after would mean the real action already
     happened by the time trust was checked, which is exactly backwards.
  2. Everything Phase4Gateway.call_tool already does (voice-gate, then
     consent, then RCAP-P, then explanation logging) — completely
     unchanged, reached only if step 1 passes.

Both checks are mandatory; this ordering does not make voice-gate or
consent optional for this tool, it only means top-trust-tier is evaluated
slightly earlier. A VOICE-channel request for full control is refused
either way — by top-trust-tier if the session also happens to be
low-trust, by Phase4Gateway's own voice-gate regardless of trust score if
it isn't. Documented explicitly here because gate-ordering choices are
exactly the kind of thing that looks arbitrary if left unexplained (same
reasoning style voice_consent_gateway/gateway.py's own docstring uses for itself).

A second gap, found by testing the kill switch live over HTTP rather than
only in isolation, and closed here: rcap_p.gateway.RCAPGateway.
grant_tool_permission (checked directly) is a bare passthrough to
rotate.grant(session_id, tool_name) — it has never, since Phase 3,
checked whether session_id corresponds to a real, ACTIVE QAP-P session.
This was harmless while nothing tied RCAP-P sessions to real QAP-P
sessions at all (Phases 3-4). It stops being harmless the moment a kill
switch exists whose entire job is making a compromised session unable to
act: without a guard, a session quarantined by the kill switch could
still be handed a brand-new ROTATE token afterward and go on calling
WRITE-tier tools (whichever ones still had standing consent — the kill
switch deliberately only revokes SENSITIVE-tier consent, see
killswitch.py), even though every token it held AT THE MOMENT the switch
was pulled was correctly revoked. Phase5Gateway.grant_tool_permission
below closes this by checking QAP-P session state BEFORE ever calling
RCAPGateway's own grant_tool_permission — new code, composed in front of
Phase 3's unmodified method, not a change to it. device_control_gateway/api.py's
/tools/grant route calls this guarded method, not RCAPGateway's directly;
see tests/test_gateway_phase5.py::test_quarantined_session_cannot_mint_a_
fresh_rotate_token for the regression test this gap earned.
"""
from typing import TYPE_CHECKING, Dict, Optional

from voice_consent_gateway.gateway import Phase4Gateway
from voice_consent_gateway.models import WriteCallRequest, WriteCallResult

from .killswitch import pull_kill_switch
from .ledger_bridge import Ledger
from .models import KillSwitchReport
from .trust_tier import TopTrustTierGate

if TYPE_CHECKING:  # pragma: no cover
    from qap_p.orchestrator import Orchestrator
    from voice_consent_gateway.consent import ConsentRegistry
    from rcap_p.models import ToolManifestEntry

FULL_CONTROL_TOOL_NAME = "remote_control_request_full_session"


class Phase5Gateway:
    def __init__(
        self,
        phase4_gateway: Phase4Gateway,
        top_trust_tier_gate: TopTrustTierGate,
        orchestrator: "Orchestrator",
        ledger: Ledger,
    ):
        self.phase4_gateway = phase4_gateway
        self.top_trust_tier_gate = top_trust_tier_gate
        self.orchestrator = orchestrator
        self.ledger = ledger

    async def call_tool(self, request: WriteCallRequest) -> WriteCallResult:
        if request.tool_name == FULL_CONTROL_TOOL_NAME:
            decision = self.top_trust_tier_gate.check(request.session_id)
            if not decision.granted:
                h = self.ledger.append("phase5_top_trust_tier_denied", {
                    "tool_name": request.tool_name,
                    "session_id": request.session_id,
                    "confirmation_channel": request.confirmation_channel.value,
                    "explanation": request.explanation,
                    "reason": decision.reason,
                    "trust_score": decision.trust_score,
                })
                return WriteCallResult(
                    tool_name=request.tool_name, ok=False, content=None,
                    denied_reason=decision.reason,
                    underlying_result=None, ledger_entry_hashes=[h],
                )

        return await self.phase4_gateway.call_tool(request)

    def grant_tool_permission(self, session_id: str, tool_name: str) -> str:
        """Guarded replacement for calling
        self.phase4_gateway.rcap_gateway.grant_tool_permission directly —
        see module docstring for the gap this closes. Raises ValueError
        (never crashes) for an unknown or non-ACTIVE session, mirroring
        how every other denial in this project is surfaced as a caller-
        facing reason rather than an unhandled exception."""
        try:
            state = self.orchestrator.get_session_state(session_id)
        except ValueError as e:
            raise ValueError(f"cannot grant a tool permission: {e}") from e
        if state.value != "ACTIVE":
            raise ValueError(
                f"cannot grant a tool permission: session {session_id} is {state.value}, not ACTIVE "
                "(if this session was quarantined by the kill switch, it stays unable to obtain new "
                "tool permissions until it is revived or recovered through QAP-P)"
            )
        return self.phase4_gateway.rcap_gateway.grant_tool_permission(session_id, tool_name)

    def pull_kill_switch(
        self,
        *,
        consent_registry: Optional["ConsentRegistry"] = None,
        registry: Optional[Dict[str, "ToolManifestEntry"]] = None,
        reason: str = "manual_trigger",
    ) -> KillSwitchReport:
        """Build plan Section 7.1: 'a kill switch that immediately
        quarantines every session and revokes every scoped token.' See
        killswitch.py's module docstring for exactly which existing Phase
        1/3/4 methods this composes — nothing here duplicates their
        logic."""
        return pull_kill_switch(
            self.orchestrator,
            self.phase4_gateway.rcap_gateway,
            self.ledger,
            consent_registry=consent_registry or self.phase4_gateway.consent_registry,
            registry=registry or self.phase4_gateway.registry,
            reason=reason,
        )
