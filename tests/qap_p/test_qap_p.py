"""
Unit tests for QAP-P Phase 1 components. Run with:
    PYTHONPATH=. pytest tests/ -v

These complement demo_exit_criteria.py, which is the narrative end-to-end
proof of the Phase 1 exit criteria; these tests focus on individual
mechanisms in isolation.
"""
import time
import sqlite3

import pytest

from qap_p.config import Config
from qap_p.orchestrator import Orchestrator
from qap_p.models import DeviceRole, NodeState
from qap_p.hardware_key import SimulatedHardwareKey
from qap_p.auth import AuthorizationError
from qap_p.vault import VaultError
from qap_p.state_machine import StateMachineError
from qap_p.quarantine import QuarantineRecoveryError


@pytest.fixture
def orch(tmp_path):
    config = Config(
        data_dir=str(tmp_path / "data"),
        quiesce_ttl_seconds=1,
        soft_recovery_cooldown_seconds=0.5,
    )
    return Orchestrator(config)


def age_device(o, device_id, days):
    conn = sqlite3.connect(o.config.db_path)
    conn.execute("UPDATE devices SET enrolled_at = ? WHERE device_id = ?",
                 (time.time() - days * 86400, device_id))
    conn.commit()
    conn.close()


# ---------------- Ledger ----------------

def test_ledger_hash_chain_intact(orch):
    orch.ledger.append("test_event", {"a": 1})
    orch.ledger.append("test_event", {"a": 2})
    ok, bad_id = orch.verify_ledger()
    assert ok
    assert bad_id is None


def test_ledger_detects_tampering(orch):
    orch.ledger.append("test_event", {"a": 1})
    conn = sqlite3.connect(orch.config.db_path)
    conn.execute('UPDATE ledger SET payload = ? WHERE id = 1', ('{"a": 999}',))
    conn.commit()
    conn.close()
    ok, bad_id = orch.verify_ledger()
    assert not ok
    assert bad_id == 1


# ---------------- Device / session lifecycle ----------------

def test_session_starts_active(orch):
    orch.enroll_device("d1", DeviceRole.LAPTOP)
    sid = orch.create_session("d1")
    assert orch.get_session_state(sid) == NodeState.ACTIVE


def test_cannot_create_session_for_unenrolled_device(orch):
    with pytest.raises(ValueError):
        orch.create_session("ghost-device")


def test_quiesce_then_ttl_expiry_auto_quarantines(orch):
    orch.enroll_device("d1", DeviceRole.LAPTOP)
    sid = orch.create_session("d1")
    orch.quiesce_session(sid, {"cpu_load": 0.1})
    assert orch.get_session_state(sid) == NodeState.QUIESCE
    time.sleep(1.3)
    expired = orch.sweep_expired_quiesce()
    assert sid in expired
    assert orch.get_session_state(sid) == NodeState.QUARANTINE


def test_cannot_quiesce_a_non_active_session(orch):
    orch.enroll_device("d1", DeviceRole.LAPTOP)
    sid = orch.create_session("d1")
    orch.quiesce_session(sid, {})
    with pytest.raises(StateMachineError):
        orch.quiesce_session(sid, {})  # already QUIESCE, not ACTIVE


# ---------------- Peer authorization ----------------

def test_low_trust_device_cannot_issue_authorization(orch):
    phone_key = SimulatedHardwareKey()
    orch.enroll_device("phone-1", DeviceRole.PHONE, phone_key)
    orch.enroll_device("laptop-1", DeviceRole.LAPTOP)
    sid = orch.create_session("laptop-1")
    orch.quiesce_session(sid, {})
    nonce = orch.request_authorization_challenge(sid)
    signature = phone_key.sign(nonce.encode())
    # phone-1 was JUST enrolled -> trust score below issuance threshold
    with pytest.raises(AuthorizationError):
        orch.peer_authorize("phone-1", sid, nonce, signature)


def test_aged_trusted_device_can_authorize_and_revive(orch):
    phone_key = SimulatedHardwareKey()
    orch.enroll_device("phone-1", DeviceRole.PHONE, phone_key)
    orch.enroll_device("laptop-1", DeviceRole.LAPTOP)
    age_device(orch, "phone-1", days=40)

    sid = orch.create_session("laptop-1")
    orch.quiesce_session(sid, {})
    nonce = orch.request_authorization_challenge(sid)
    signature = phone_key.sign(nonce.encode())
    token = orch.peer_authorize("phone-1", sid, nonce, signature)
    state = orch.revive_session(sid, token)
    assert state == NodeState.ACTIVE


def test_nonce_cannot_be_replayed(orch):
    phone_key = SimulatedHardwareKey()
    orch.enroll_device("phone-1", DeviceRole.PHONE, phone_key)
    orch.enroll_device("laptop-1", DeviceRole.LAPTOP)
    age_device(orch, "phone-1", days=40)

    sid = orch.create_session("laptop-1")
    orch.quiesce_session(sid, {})
    nonce = orch.request_authorization_challenge(sid)
    signature = phone_key.sign(nonce.encode())
    orch.peer_authorize("phone-1", sid, nonce, signature)
    with pytest.raises(AuthorizationError):
        orch.peer_authorize("phone-1", sid, nonce, signature)


def test_forged_signature_is_rejected(orch):
    phone_key = SimulatedHardwareKey()
    attacker_key = SimulatedHardwareKey()
    orch.enroll_device("phone-1", DeviceRole.PHONE, phone_key)
    orch.enroll_device("laptop-1", DeviceRole.LAPTOP)
    age_device(orch, "phone-1", days=40)

    sid = orch.create_session("laptop-1")
    orch.quiesce_session(sid, {})
    nonce = orch.request_authorization_challenge(sid)
    forged_signature = attacker_key.sign(nonce.encode())  # signed by the WRONG key
    with pytest.raises(AuthorizationError):
        orch.peer_authorize("phone-1", sid, nonce, forged_signature)


def test_spoofed_reauth_token_is_rejected_and_quarantines(orch):
    orch.enroll_device("laptop-1", DeviceRole.LAPTOP)
    sid = orch.create_session("laptop-1")
    with pytest.raises(AuthorizationError):
        orch.revive_session(sid, "not.a.real.token")
    assert orch.get_session_state(sid) == NodeState.QUARANTINE


# ---------------- Quarantine recovery ----------------

def test_soft_recovery_requires_biometric(orch):
    orch.enroll_device("laptop-1", DeviceRole.LAPTOP)
    sid = orch.create_session("laptop-1")
    orch.quarantine_session(sid, reason="test")
    ok = orch.recover_soft(sid, is_enrolled_device=True, impossible_travel=False, biometric_verified=False)
    assert ok is False
    assert orch.get_session_state(sid) == NodeState.QUARANTINE


def test_soft_recovery_respects_cooldown(orch):
    orch.enroll_device("laptop-1", DeviceRole.LAPTOP)
    sid = orch.create_session("laptop-1")
    orch.quarantine_session(sid, reason="test")
    assert orch.recover_soft(sid, True, False, True) is True
    orch.quarantine_session(sid, reason="test-again")
    # immediately retrying should be blocked by the cooldown window
    assert orch.recover_soft(sid, True, False, True) is False


def test_hard_tier_required_for_unenrolled_or_impossible_travel(orch):
    orch.enroll_device("laptop-1", DeviceRole.LAPTOP)
    sid = orch.create_session("laptop-1")
    orch.quarantine_session(sid, reason="test")
    with pytest.raises(QuarantineRecoveryError):
        orch.recover_soft(sid, is_enrolled_device=False, impossible_travel=False, biometric_verified=True)
    with pytest.raises(QuarantineRecoveryError):
        orch.recover_soft(sid, is_enrolled_device=True, impossible_travel=True, biometric_verified=True)


def test_hard_recovery_requires_correct_key(orch):
    orch.enroll_device("laptop-1", DeviceRole.LAPTOP)
    sid = orch.create_session("laptop-1")
    orch.quarantine_session(sid, reason="test")
    assert orch.recover_hard(sid, "wrong-key") is False
    assert orch.get_session_state(sid) == NodeState.QUARANTINE
    real_key = open(orch.config.offline_recovery_key_path).read().strip()
    assert orch.recover_hard(sid, real_key) is True
    assert orch.get_session_state(sid) == NodeState.ACTIVE


# ---------------- Vault ----------------

def test_token_round_trip(orch):
    orch.enroll_device("laptop-1", DeviceRole.LAPTOP)
    token = orch.issue_token("laptop-1", scope="demo:read", ttl_seconds=60)
    payload = orch.verify_token(token)
    assert payload["scope"] == "demo:read"
    assert payload["device_id"] == "laptop-1"


def test_token_expires(orch):
    orch.enroll_device("laptop-1", DeviceRole.LAPTOP)
    token = orch.issue_token("laptop-1", scope="demo:read", ttl_seconds=0.5)
    time.sleep(0.7)
    with pytest.raises(VaultError):
        orch.verify_token(token)


def test_token_revocation(orch):
    orch.enroll_device("laptop-1", DeviceRole.LAPTOP)
    token = orch.issue_token("laptop-1", scope="demo:read", ttl_seconds=300)
    orch.vault.revoke_all_for_device("laptop-1")
    with pytest.raises(VaultError):
        orch.verify_token(token)


# ---------------- Canary ----------------

def test_canary_touch_quarantines_the_accessing_session(orch):
    orch.enroll_device("browser-1", DeviceRole.BROWSER)
    sid = orch.create_session("browser-1")
    value = orch.plant_canary()
    triggered = orch.check_canary(value, sid)
    assert triggered is True
    assert orch.get_session_state(sid) == NodeState.QUARANTINE


def test_wrong_value_does_not_trigger_canary(orch):
    orch.enroll_device("browser-1", DeviceRole.BROWSER)
    sid = orch.create_session("browser-1")
    orch.plant_canary()
    triggered = orch.check_canary("not-the-canary-value", sid)
    assert triggered is False
    assert orch.get_session_state(sid) == NodeState.ACTIVE


# ---------------- Trust score ----------------

def test_freshly_enrolled_device_is_below_issuance_threshold(orch):
    orch.enroll_device("d1", DeviceRole.PHONE)
    score = orch.compute_trust_score("d1")
    assert score < orch.config.authorization_issuance_threshold


def test_aged_clean_device_clears_issuance_threshold(orch):
    orch.enroll_device("d1", DeviceRole.PHONE)
    age_device(orch, "d1", days=40)
    score = orch.compute_trust_score("d1")
    assert score >= orch.config.authorization_issuance_threshold


# ---------------- Chaos engineering ----------------

def test_chaos_spoofed_reauth_suite_passes(orch):
    orch.enroll_device("laptop-1", DeviceRole.LAPTOP)
    sid = orch.create_session("laptop-1")
    results = orch.run_chaos_suite(sid)
    assert all(r["passed"] for r in results)
