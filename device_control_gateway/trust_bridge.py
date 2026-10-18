"""
QapPTrustLookup — a real qap_p.orchestrator.Orchestrator-backed
implementation of rcap_p.fastlane.TrustLookup.

This closes an open item flagged explicitly in Phase 3's own README
("Wire a real qap_p.orchestrator.Orchestrator-backed TrustLookup into
RCAPGateway(..., trust_lookup=...) so FASTLANE actually evaluates real
sessions instead of NullTrustLookup's always-False default.") — checked
directly while building Phase 5, not assumed: neither rcap_p/api.py nor
voice_consent_gateway/api.py passes a `trust_lookup` when constructing RCAPGateway, so in
every deployment built so far, FASTLANE has been permanently disabled
(safe, but not yet real). It was optional right up until now; Phase 5's
own top-trust-tier gate (trust_tier.py) needs real trust-score evaluation
to mean anything for a capability ("full laptop control") that is no
longer a demo fixture standing in for something imaginary — so this stops
being deferrable here.

Design note: this class does NOT modify rcap_p.fastlane.TrustLookup or its
Protocol shape at all — it is a new implementation of an interface Phase 3
already defined, imported by Phase 5, exactly the composition-not-
modification discipline every earlier phase's README insists on for
itself. qap_p is imported directly (not vendored) because trust scoring is
inherently a Phase-1-owned computation; there's no honest vendored
fallback for "what is this device's trust score" the way there is for a
generic hash-chain ledger.

Fail-safe, not fail-crash: qap_p.orchestrator.Orchestrator.get_session_state
and .compute_trust_score both raise ValueError for an unknown session/
device (checked directly in orchestrator.py, not assumed) rather than
returning a sentinel. Every method below catches that and returns the same
"not trusted" answer NullTrustLookup would give — an unrecognized session
must never be treated as an error that crashes the gate; it must be
treated as "obviously not trusted enough," the same conservative default
this project uses everywhere else a trust question can't be answered with
confidence.
"""
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - import-time type hint only
    from qap_p.orchestrator import Orchestrator


class QapPTrustLookup:
    """Wraps a live Orchestrator instance. Constructed once per daemon
    process and shared between RCAP-P's own FastlaneGate and Phase 5's
    TopTrustTierGate, so both ask the exact same underlying Orchestrator
    the exact same questions — one trust computation, two thresholds
    (see trust_tier.py's module docstring for why two thresholds is
    correct, not redundant)."""

    def __init__(self, orchestrator: "Orchestrator"):
        self.orchestrator = orchestrator

    def get_session_state(self, session_id: str) -> str:
        try:
            state = self.orchestrator.get_session_state(session_id)
        except ValueError:
            return "UNKNOWN"
        return state.value if hasattr(state, "value") else str(state)

    def compute_trust_score(self, device_id: str) -> float:
        try:
            return self.orchestrator.compute_trust_score(device_id)
        except ValueError:
            return 0.0

    def get_device_id_for_session(self, session_id: str) -> Optional[str]:
        session = self.orchestrator.store.get_session(session_id)
        return session.device_id if session is not None else None

    def is_hardware_backed(self, device_id: str) -> bool:
        device = self.orchestrator.get_device(device_id)
        return bool(device.hardware_backed) if device is not None else False
