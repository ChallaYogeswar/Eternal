"""
Top trust tier — Eternal_Master_Build_Plan.md Section 7.1, verbatim:
"'take over full control of my laptop' is the single most sensitive
action in the whole system — the top trust tier, non-voice confirmation
always required, plus a kill switch that immediately quarantines every
session and revokes every scoped token."

This is DELIBERATELY a stricter, separate threshold from RCAP-P's own
FASTLANE threshold (rcap_p.config.Config.fastlane_min_trust_score, default
0.6) even though both ultimately read the same underlying qap_p trust
score through the same QapPTrustLookup (trust_bridge.py). FASTLANE answers
"is this session trustworthy enough to skip PERIMETER/INSPECT/ROTATE/
DECEIVE friction for an ORDINARY call." Top-trust-tier answers a
narrower, harder question: "is this session trustworthy enough to hand
over the keyboard, mouse, and screen of a real computer." Collapsing these
into one number would mean either (a) FASTLANE becomes needlessly hard to
reach for routine calls, or (b) full laptop control becomes reachable at
an ordinary FASTLANE trust level — both wrong, so this is a second,
independently-configured gate, not a re-use of FastlaneGate with a bigger
number swapped in.

The actual score-vs-threshold comparison is qap_p.trust.gates_sensitive_
tier(score, threshold) — imported directly, not reimplemented. That
function's own docstring names "full laptop control" explicitly, written
in Phase 1, four phases before anything called it: "sensitive-tier actions
(trade execution, full laptop control, physical Home Assistant actions)
require a trust score above this threshold, regardless of anything else
being authenticated." This module supplies the threshold, the surrounding
session/device/hardware checks (same shape FastlaneGate already
established — see rcap_p/fastlane.py), and the plumbing to call it from
Phase 5's own gateway, without modifying qap_p or rcap_p at all.

Audit E-02 (fixed): `Device.hardware_backed` used to be set True for every enrolled device, so the hardware check
below passed for simulated and client-supplied keys. It is now set only from a verified platform attestation
(`qap_p.attestation.AttestationVerifier`, fail-closed by default). Until a real verifier is installed NO device is
hardware-backed, so this gate refuses full device control for everyone: the honest state, not a bug.
"""
from dataclasses import dataclass
from typing import Optional

from qap_p.trust import gates_sensitive_tier
from rcap_p.fastlane import NullTrustLookup, TrustLookup

from .config import Config


@dataclass
class TopTrustTierDecision:
    granted: bool
    reason: str
    trust_score: Optional[float] = None


class TopTrustTierGate:
    def __init__(self, config: Config, trust_lookup: Optional[TrustLookup] = None):
        self.config = config
        # Same fail-safe default FastlaneGate uses: no real trust backend
        # supplied -> every check answers "not trusted" rather than
        # guessing. A disabled gate is a safe failure; a fabricated one
        # is not (rcap_p/fastlane.py's own module docstring, same logic
        # applies here).
        self.trust_lookup = trust_lookup or NullTrustLookup()

    def check(self, session_id: Optional[str]) -> TopTrustTierDecision:
        if session_id is None:
            return TopTrustTierDecision(False, "no session_id presented; full device control requires an identified QAP-P session")

        state = self.trust_lookup.get_session_state(session_id)
        if state != "ACTIVE":
            return TopTrustTierDecision(False, f"session state is {state}, not ACTIVE")

        device_id = self.trust_lookup.get_device_id_for_session(session_id)
        if device_id is None:
            return TopTrustTierDecision(False, "session has no associated enrolled device")

        if not self.trust_lookup.is_hardware_backed(device_id):
            return TopTrustTierDecision(False, "device is not hardware-backed; full device control requires hardware attestation")

        score = self.trust_lookup.compute_trust_score(device_id)
        if not gates_sensitive_tier(score, self.config.top_trust_tier_min_score):
            return TopTrustTierDecision(
                False,
                f"trust score {score:.2f} below the top-trust-tier threshold "
                f"{self.config.top_trust_tier_min_score:.2f} required for full device control "
                "(build plan Section 7.1); this is stricter than ordinary FASTLANE eligibility",
                trust_score=score,
            )
        return TopTrustTierDecision(True, f"top trust tier cleared (score {score:.2f})", trust_score=score)
