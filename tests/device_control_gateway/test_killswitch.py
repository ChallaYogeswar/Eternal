"""
Unit tests for device_control_gateway.killswitch.pull_kill_switch — the build plan 7.1
guarantee: "immediately quarantines every session and revokes every
scoped token." Every field of KillSwitchReport is checked against real
QAP-P/RCAP-P state, not just an ok=True.
"""
import pytest

from device_control_gateway.killswitch import pull_kill_switch
from rcap_p.models import ToolSensitivity


def test_no_devices_is_a_clean_noop(orchestrator, rcap_gateway, ledger):
    report = pull_kill_switch(orchestrator, rcap_gateway, ledger, reason="test")
    assert report.devices_seen == 0
    assert report.sessions_quarantined == []
    assert report.vault_tokens_revoked == 0
    assert report.rotate_tokens_revoked == 0


def test_quarantines_every_active_session_across_every_device(orchestrator, rcap_gateway, ledger, enroll_and_activate):
    _, session_a = enroll_and_activate(device_id="dev-a")
    _, session_b = enroll_and_activate(device_id="dev-b")

    report = pull_kill_switch(orchestrator, rcap_gateway, ledger, reason="test")

    assert report.devices_seen == 2
    assert set(report.sessions_quarantined) == {session_a, session_b}
    assert orchestrator.get_session_state(session_a).value == "QUARANTINE"
    assert orchestrator.get_session_state(session_b).value == "QUARANTINE"


def test_already_quarantined_session_is_not_double_counted(orchestrator, rcap_gateway, ledger, enroll_and_activate):
    _, session_id = enroll_and_activate()
    orchestrator.quarantine_session(session_id, reason="pre-existing")

    report = pull_kill_switch(orchestrator, rcap_gateway, ledger, reason="test")

    assert session_id in report.sessions_already_quarantined
    assert session_id not in report.sessions_quarantined


def test_revokes_vault_tokens(orchestrator, rcap_gateway, ledger, enroll_and_activate):
    device, session_id = enroll_and_activate()
    orchestrator.issue_token(device.device_id, scope="test-scope")
    orchestrator.issue_token(device.device_id, scope="test-scope-2")

    report = pull_kill_switch(orchestrator, rcap_gateway, ledger, reason="test")
    assert report.vault_tokens_revoked == 2


def test_revokes_rotate_tokens(orchestrator, rcap_gateway, ledger, enroll_and_activate):
    _, session_id = enroll_and_activate()
    rcap_gateway.grant_tool_permission(session_id, "ha_unlock")
    rcap_gateway.grant_tool_permission(session_id, "laptop_lock")

    report = pull_kill_switch(orchestrator, rcap_gateway, ledger, reason="test")
    assert report.rotate_tokens_revoked == 2


def test_revokes_consent_for_sensitive_tools_only(orchestrator, rcap_gateway, ledger, consent_registry, registry, enroll_and_activate):
    enroll_and_activate()
    consent_registry.grant("ha_unlock", granted_by="test")     # SENSITIVE
    consent_registry.grant("laptop_lock", granted_by="test")   # WRITE

    assert registry["ha_unlock"].sensitivity == ToolSensitivity.SENSITIVE
    assert registry["laptop_lock"].sensitivity == ToolSensitivity.WRITE

    report = pull_kill_switch(orchestrator, rcap_gateway, ledger,
                               consent_registry=consent_registry, registry=registry, reason="test")

    assert report.consent_revoked_for_tools == ["ha_unlock"]
    assert consent_registry.has_consent("ha_unlock") is False
    assert consent_registry.has_consent("laptop_lock") is True  # untouched


def test_skips_consent_step_when_not_provided(orchestrator, rcap_gateway, ledger, enroll_and_activate):
    """consent_registry/registry are optional — a QAP-P/RCAP-P-only
    caller (no Phase 4 wired in) still gets a working kill switch."""
    enroll_and_activate()
    report = pull_kill_switch(orchestrator, rcap_gateway, ledger, reason="test")
    assert report.consent_revoked_for_tools == []


def test_writes_a_ledger_entry(orchestrator, rcap_gateway, ledger, enroll_and_activate):
    """Not an exact '+1' delta: rcap_p.rotate.RotateRing.revoke_session
    (Phase 3, unmodified, checked directly) also writes its own ledger
    entry per session as part of what it means for tokens to be
    genuinely revoked — that's additional real audit trail, not noise to
    suppress. What matters here is that pull_kill_switch's OWN summary
    entry exists, with the right event_type, reason, and hash."""
    enroll_and_activate()
    count_before = ledger.count()
    report = pull_kill_switch(orchestrator, rcap_gateway, ledger, reason="my_reason")
    assert ledger.count() > count_before
    assert report.ledger_entry_hash
    matching = [e for e in ledger.recent(20) if e["hash"] == report.ledger_entry_hash]
    assert len(matching) == 1
    assert matching[0]["event_type"] == "phase5_kill_switch_pulled"
    assert matching[0]["payload"]["reason"] == "my_reason"


def test_ledger_stays_intact_after_kill_switch(orchestrator, rcap_gateway, ledger, enroll_and_activate):
    enroll_and_activate()
    pull_kill_switch(orchestrator, rcap_gateway, ledger, reason="test")
    ok, bad_id = ledger.verify_chain()
    assert ok is True
    assert bad_id is None


def test_reason_is_recorded_verbatim_and_prefixed_in_qap_p(orchestrator, rcap_gateway, ledger, enroll_and_activate):
    """The QAP-P-side quarantine reason should make clear it came from the
    kill switch specifically, not an ordinary quarantine event, so a
    human reading QAP-P's own ledger later can tell the two apart."""
    _, session_id = enroll_and_activate()
    pull_kill_switch(orchestrator, rcap_gateway, ledger, reason="suspected_theft")
    qap_entries = [e for e in orchestrator.ledger_entries(50) if e.get("event_type") == "quarantine_triggered"]
    matching = [e for e in qap_entries if e["payload"].get("session_id") == session_id]
    assert matching
    assert "KILL_SWITCH" in matching[0]["payload"]["reason"]
    assert "suspected_theft" in matching[0]["payload"]["reason"]
