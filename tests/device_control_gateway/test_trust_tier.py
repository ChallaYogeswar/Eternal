"""
Unit tests for device_control_gateway.trust_tier.TopTrustTierGate — the gate protecting
'take over full control of my laptop' (build plan Section 7.1).
"""
import pytest

from device_control_gateway.trust_tier import TopTrustTierGate
from rcap_p.fastlane import NullTrustLookup


def test_no_session_id_denied(phase5_config):
    gate = TopTrustTierGate(phase5_config)
    decision = gate.check(None)
    assert decision.granted is False
    assert "no session_id" in decision.reason


def test_null_trust_lookup_always_denies(phase5_config):
    """Same fail-safe default as FastlaneGate: no real trust backend
    wired up -> refuse, never guess."""
    gate = TopTrustTierGate(phase5_config, NullTrustLookup())
    decision = gate.check("any-session")
    assert decision.granted is False


def test_freshly_enrolled_device_does_not_clear_the_bar(top_trust_tier_gate, enroll_and_activate):
    """A brand-new device's trust score must be well below the top-tier
    threshold — checked against the REAL scoring model (qap_p/trust.py),
    not a stub. This is the same live behavior confirmed manually against
    the running HTTP daemon during development (trust score 0.40 vs.
    threshold 0.85)."""
    device, session_id = enroll_and_activate()
    decision = top_trust_tier_gate.check(session_id)
    assert decision.granted is False
    assert "hardware" in decision.reason          # refused at the hardware check now, before the score is even read


def test_quarantined_session_denied(top_trust_tier_gate, enroll_and_activate, orchestrator):
    device, session_id = enroll_and_activate()
    orchestrator.quarantine_session(session_id, reason="test")
    decision = top_trust_tier_gate.check(session_id)
    assert decision.granted is False
    assert "ACTIVE" in decision.reason


def test_unknown_session_denied(top_trust_tier_gate):
    decision = top_trust_tier_gate.check("does-not-exist")
    assert decision.granted is False


def test_high_trust_device_clears_the_bar(top_trust_tier_gate, orchestrator, phase5_config):
    """Exercise the OTHER side of the gate too, not just the denial path.
    qap_p's real trust formula (qap_p/trust.py, unmodified, checked
    directly): baseline 0.30, +0.25 once enrollment_age_days >= 30,
    +0.10 for hardware_backed (always True on enrollment — see
    test_trust_bridge.py), +0.25 once good_standing_days >= 14 (which
    equals enrollment_age_days whenever quarantine_count is 0). A device
    enrolled 35 days ago with no quarantine history legitimately scores
    0.30+0.25+0.10+0.25=0.90 under that unmodified formula — this
    constructs exactly that device via Store.create_device (a real,
    public QAP-P method) with a backdated enrolled_at, rather than
    waiting 35 real days or inventing a setter QAP-P doesn't have."""
    from qap_p.models import Device, DeviceRole
    import time

    device = Device(
        device_id="long-standing-device",
        role=DeviceRole.LAPTOP,
        public_key_pem="-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----",
        hardware_backed=True,
        attestation_type="test-fixture",
        enrolled_at=time.time() - 35 * 86400,
        quarantine_count=0,
        anomaly_count=0,
    )
    orchestrator.store.create_device(device)
    session_id = orchestrator.create_session("long-standing-device")

    score = orchestrator.compute_trust_score("long-standing-device")
    assert score >= phase5_config.top_trust_tier_min_score, (
        f"test setup should legitimately clear the bar under qap_p's real formula; got {score:.2f}"
    )

    decision = top_trust_tier_gate.check(session_id)
    assert decision.granted is True
    assert decision.trust_score == score


def test_simulated_key_never_clears_the_hardware_gate_regardless_of_score(phase5_config, orchestrator, trust_lookup):
    """Audit E-02 regression: even a long-standing, zero-incident device with a high trust score is refused when its
    key was never verified as hardware-backed."""
    import time
    from device_control_gateway.trust_tier import TopTrustTierGate
    from qap_p.models import DeviceRole
    d = orchestrator.enroll_device("old-dev", DeviceRole("laptop"))
    import sqlite3
    c = sqlite3.connect(orchestrator.config.db_path)
    c.execute("UPDATE devices SET enrolled_at=? WHERE device_id=?", (time.time() - 400 * 86400, "old-dev"))
    c.commit(); c.close()
    sid = orchestrator.create_session("old-dev")
    assert orchestrator.compute_trust_score("old-dev") > 0.7        # high score; only the hardware factor is missing
    decision = TopTrustTierGate(phase5_config, trust_lookup).check(sid)
    assert decision.granted is False and "hardware" in decision.reason


def test_verified_hardware_with_high_score_can_clear_the_gate(phase5_config, attested_orchestrator):
    import sqlite3, time
    from device_control_gateway.trust_bridge import QapPTrustLookup
    from device_control_gateway.trust_tier import TopTrustTierGate
    from qap_p.models import DeviceRole
    o = attested_orchestrator
    o.enroll_device("hw-dev", DeviceRole("laptop"))
    c = sqlite3.connect(o.config.db_path)
    c.execute("UPDATE devices SET enrolled_at=? WHERE device_id=?", (time.time() - 400 * 86400, "hw-dev"))
    c.commit(); c.close()
    sid = o.create_session("hw-dev")
    decision = TopTrustTierGate(phase5_config, QapPTrustLookup(o)).check(sid)
    assert decision.granted is True, decision.reason
