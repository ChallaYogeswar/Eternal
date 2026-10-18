"""
FASTLANE.

RCAP whitepaper Section 3.5: "A session presenting a valid mutual-TLS
certificate and, where required by deployment policy, hardware
attestation, is routed via FASTLANE directly to CORE, bypassing Rings 1
through 4 entirely." Eternal_Master_Build_Plan.md: "You, via passkey,
always frictionless. Every automated routine — including ones you wrote
yourself — goes through the full ring stack, no exceptions."

That second sentence is the one this module exists to enforce correctly.
It would be easy to build FASTLANE as "does this session belong to the
owner's enrolled device with a good trust score" and stop there — but a
scheduled job, a proactive trigger, or an agentic multi-step tool chain
can run *from* the owner's own enrolled laptop, at a high trust score,
without the owner being at the keyboard for that specific call. The build
plan is explicit that this must still go through PERIMETER/INSPECT/
ROTATE/DECEIVE like anything else. So eligibility here checks CallerKind
first, as a hard gate, before it ever looks at trust score:

    CallerKind.AUTOMATED_ROUTINE -> never FASTLANE, regardless of trust score.
    CallerKind.OWNER_INTERACTIVE -> FASTLANE only if the QAP-P session
                                     backing this call is ACTIVE, hardware-
                                     backed, and above the trust threshold.

This is also the concrete integration point with Phase 1: "passkey" at
personal scale IS the QAP-P session/device state Phase 1 already built —
there is no second passkey system to invent. If qap_p isn't installed
alongside RCAP-P (standalone Phase 3 testing), FASTLANE always evaluates
to False rather than guessing — see TrustLookup's fallback below. A
disabled fast lane is a safe failure; a fabricated one is not.
"""
from dataclasses import dataclass
from typing import Optional, Protocol

from .config import Config
from .models import CallerKind, ToolCallContext


class TrustLookup(Protocol):
    """Whatever object the gateway is handed to answer "is this session
    trustworthy enough for FASTLANE" — in an integrated deployment this is
    backed by a real qap_p.orchestrator.Orchestrator instance (its
    get_session_state()/compute_trust_score() methods); in standalone
    testing it's a small fake. Structural typing (Protocol) rather than a
    concrete base class so nothing here needs to import qap_p at all."""

    def get_session_state(self, session_id: str) -> str: ...
    def compute_trust_score(self, device_id: str) -> float: ...
    def get_device_id_for_session(self, session_id: str) -> Optional[str]: ...
    def is_hardware_backed(self, device_id: str) -> bool: ...


class NullTrustLookup:
    """Used when no real trust backend is supplied. Every check returns the
    safe answer (not trusted), so RCAP-P run standalone with no Phase 1
    installed simply never grants FASTLANE — everything goes through the
    full ring stack, which is the conservative default, not a placeholder
    that happens to work."""

    def get_session_state(self, session_id: str) -> str:
        return "UNKNOWN"

    def compute_trust_score(self, device_id: str) -> float:
        return 0.0

    def get_device_id_for_session(self, session_id: str) -> Optional[str]:
        return None

    def is_hardware_backed(self, device_id: str) -> bool:
        return False


@dataclass
class FastlaneDecision:
    granted: bool
    reason: str = ""


class FastlaneGate:
    def __init__(self, config: Config, trust_lookup: Optional[TrustLookup] = None):
        self.config = config
        self.trust_lookup = trust_lookup or NullTrustLookup()

    def check(self, ctx: ToolCallContext) -> FastlaneDecision:
        if ctx.caller_kind != CallerKind.OWNER_INTERACTIVE:
            return FastlaneDecision(
                granted=False,
                reason="FASTLANE never applies to automated routines, regardless of trust score "
                       "(build plan: 'no exceptions')",
            )
        if ctx.session_id is None:
            return FastlaneDecision(granted=False, reason="no session_id to check trust against")

        state = self.trust_lookup.get_session_state(ctx.session_id)
        if state != "ACTIVE":
            return FastlaneDecision(granted=False, reason=f"session state is {state}, not ACTIVE")

        device_id = self.trust_lookup.get_device_id_for_session(ctx.session_id)
        if device_id is None:
            return FastlaneDecision(granted=False, reason="session has no associated enrolled device")

        if not self.trust_lookup.is_hardware_backed(device_id):
            return FastlaneDecision(granted=False, reason="device is not hardware-backed")

        score = self.trust_lookup.compute_trust_score(device_id)
        if score < self.config.fastlane_min_trust_score:
            return FastlaneDecision(
                granted=False,
                reason=f"trust score {score:.2f} below FASTLANE threshold "
                       f"{self.config.fastlane_min_trust_score:.2f}",
            )
        return FastlaneDecision(granted=True, reason=f"owner-interactive, ACTIVE, trust {score:.2f}")
