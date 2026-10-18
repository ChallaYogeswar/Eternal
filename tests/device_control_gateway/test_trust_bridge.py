"""
Unit tests for device_control_gateway.trust_bridge.QapPTrustLookup — every method checked
against a REAL qap_p.orchestrator.Orchestrator (not a mock), including the
fail-safe behavior for unknown sessions/devices that orchestrator.py's own
methods surface as ValueError rather than a sentinel (see trust_bridge.py's
module docstring for why this distinction matters).
"""
import pytest


def test_unknown_session_reports_unknown_state(trust_lookup):
    assert trust_lookup.get_session_state("no-such-session") == "UNKNOWN"


def test_unknown_device_reports_zero_trust(trust_lookup):
    assert trust_lookup.compute_trust_score("no-such-device") == 0.0


def test_unknown_session_has_no_device(trust_lookup):
    assert trust_lookup.get_device_id_for_session("no-such-session") is None


def test_unknown_device_is_not_hardware_backed(trust_lookup):
    assert trust_lookup.is_hardware_backed("no-such-device") is False


def test_real_enrolled_session_reports_active(trust_lookup, enroll_and_activate):
    device, session_id = enroll_and_activate()
    assert trust_lookup.get_session_state(session_id) == "ACTIVE"


def test_real_device_id_resolves_from_session(trust_lookup, enroll_and_activate):
    device, session_id = enroll_and_activate(device_id="dev-42")
    assert trust_lookup.get_device_id_for_session(session_id) == "dev-42"


def test_real_device_trust_score_is_a_real_float(trust_lookup, enroll_and_activate, orchestrator):
    device, session_id = enroll_and_activate()
    score = trust_lookup.compute_trust_score(device.device_id)
    assert isinstance(score, float)
    # not exact equality: qap_p's formula is wall-clock-based
    # (enrollment_age_days derives from time.time() at call time), so two
    # calls microseconds apart legitimately differ in the last few
    # decimal places — pytest.approx is the correct comparison here, not
    # a workaround for a bug.
    assert score == pytest.approx(orchestrator.compute_trust_score(device.device_id), abs=1e-6)


def test_quarantined_session_state_reflects_in_lookup(trust_lookup, enroll_and_activate, orchestrator):
    device, session_id = enroll_and_activate()
    orchestrator.quarantine_session(session_id, reason="test")
    assert trust_lookup.get_session_state(session_id) == "QUARANTINE"


def test_hardware_backed_reports_true_for_any_enrolled_device(trust_lookup, enroll_and_activate):
    """Phase 1's own enroll_device (checked directly, not assumed) sets
    hardware_backed=True unconditionally for every device, including one
    using SimulatedHardwareKey — the real/simulated distinction lives only
    in the attestation_type STRING ('simulated-dev-key (NOT hardware-
    backed...)'), not in this boolean field. QapPTrustLookup.
    is_hardware_backed faithfully reports whatever Phase 1 actually
    stores; it does not (and should not) second-guess it. See
    device_control_gateway/trust_tier.py's module docstring for what this means for the
    top-trust-tier gate's real-world strength today versus once Phase 1's
    hardware-attestation seam is filled in for real."""
    device, session_id = enroll_and_activate()
    assert trust_lookup.is_hardware_backed(device.device_id) is True
    assert "NOT hardware-backed" in device.attestation_type
