"""
Trust score model.

QAP whitepaper Section 3.4, mapped to personal scale in
Eternal_Master_Build_Plan.md Section 6.1: "Per-device/session score:
enrollment age, quarantine history, anomaly count, hardware-key presence.
Gates the sensitive-action tier directly — a low-trust session can't
authorize full laptop control or trade execution even if otherwise
authenticated."

The score is always recomputed from underlying facts (the device's
enrollment record plus ledger-derived counters) rather than stored as an
opaque running number, so it stays auditable — you can always explain *why*
a score is what it is, from the same ledger everything else is logged to.

Known Phase-1 simplification: `recent_quiesce_claims` is not yet windowed
(no rolling-window counting exists yet), and `good_standing_days` collapses
to 0 the moment ANY quarantine has ever happened rather than counting days
*since the last one*. Both are fine approximations for proving the mechanism
works; revisit once there's real usage history to tune against.
"""
from dataclasses import dataclass


@dataclass
class TrustFactors:
    enrollment_age_days: float
    quarantine_count: int
    anomaly_count: int
    hardware_backed: bool
    recent_quiesce_claims: int
    good_standing_days: float


_BASELINE = 0.30

# Weights are intentionally conservative and documented rather than tuned —
# Phase 1's job is to prove the mechanism works, not hand-tune weights
# against real usage data that doesn't exist yet.
_WEIGHTS = {
    "enrollment_age": 0.25,
    "quarantine_history": -0.35,
    "anomaly_count": -0.20,
    "hardware_backed": 0.10,
    "quiesce_frequency": -0.10,
    "good_standing": 0.25,
}


def compute_trust_score(factors: TrustFactors) -> float:
    score = _BASELINE
    score += _WEIGHTS["enrollment_age"] * min(factors.enrollment_age_days / 30.0, 1.0)
    score += _WEIGHTS["quarantine_history"] * min(factors.quarantine_count / 3.0, 1.0)
    score += _WEIGHTS["anomaly_count"] * min(factors.anomaly_count / 5.0, 1.0)
    score += _WEIGHTS["hardware_backed"] * (1.0 if factors.hardware_backed else 0.0)
    score += _WEIGHTS["quiesce_frequency"] * min(factors.recent_quiesce_claims / 10.0, 1.0)
    score += _WEIGHTS["good_standing"] * min(factors.good_standing_days / 14.0, 1.0)
    return max(0.0, min(1.0, score))


def gates_sensitive_tier(score: float, threshold: float) -> bool:
    """Build plan Section 4: sensitive-tier actions (trade execution, full
    laptop control, physical Home Assistant actions) require a trust score
    above this threshold, regardless of anything else being authenticated."""
    return score >= threshold


def can_issue_authorization(score: float, threshold: float) -> bool:
    """QAP whitepaper Section 3.3: nodes below the issuance threshold —
    including recently onboarded nodes and nodes recently out of
    QUARANTINE — cannot issue Authorizations to peers."""
    return score >= threshold
