"""
Phase 1 exit-criteria demonstration.

From Eternal_Master_Build_Plan.md, Section 8:

    "Exit criteria: you can lock/quarantine/revive a session end-to-end
    using only your phone as authorizer, every transition logged, and a
    spoofed reauth attempt reliably triggers QUARANTINE."

This script plays out that whole scenario against a live Orchestrator and
prints a pass/fail line for each checkpoint, using only the phone's signed
authority (or its biometric confirmation, or the offline recovery key it
would carry) to move the laptop's session between states — never a direct
database edit or admin backdoor.

Run with:
    python scripts/demo_exit_criteria.py
"""
import os
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qap_p.config import Config
from qap_p.orchestrator import Orchestrator
from qap_p.models import DeviceRole, NodeState
from qap_p.hardware_key import SimulatedHardwareKey
from qap_p.auth import AuthorizationError
from qap_p.vault import VaultError
from qap_p.quarantine import QuarantineRecoveryError

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

checks = []


def check(label, condition):
    checks.append((label, bool(condition)))
    print(f"  [{PASS if condition else FAIL}] {label}")


def section(title):
    print(f"\n=== {title} ===")


def age_device(config, device_id, days):
    """Demo-only convenience: backdates a device's enrollment so it clears
    the Authorization-issuance trust threshold, simulating 'this phone has
    been in good standing for a while' without an actual multi-week wait."""
    conn = sqlite3.connect(config.db_path)
    conn.execute("UPDATE devices SET enrolled_at = ? WHERE device_id = ?",
                 (time.time() - days * 86400, device_id))
    conn.commit()
    conn.close()


def main():
    data_dir = "./demo_data"
    if os.path.exists(data_dir):
        shutil.rmtree(data_dir)

    config = Config(data_dir=data_dir, quiesce_ttl_seconds=2, soft_recovery_cooldown_seconds=1)
    orch = Orchestrator(config)

    # ---- setup: enroll phone + laptop, each with its own simulated hardware key ----
    section("Setup — enroll devices")
    phone_key = SimulatedHardwareKey()
    laptop_key = SimulatedHardwareKey()

    phone = orch.enroll_device("phone-1", DeviceRole.PHONE, phone_key)
    laptop = orch.enroll_device("laptop-1", DeviceRole.LAPTOP, laptop_key)
    print(f"  enrolled phone-1  ({phone.attestation_type})")
    print(f"  enrolled laptop-1 ({laptop.attestation_type})")

    # A freshly-enrolled device must NOT be able to authorize peers yet
    # (QAP whitepaper Section 3.3). Prove that first, then age the phone up
    # to demonstrate the flow once it legitimately can.
    fresh_trust = orch.compute_trust_score("phone-1")
    check(f"freshly-enrolled phone starts below the issuance threshold "
          f"({fresh_trust:.2f} < {config.authorization_issuance_threshold})",
          fresh_trust < config.authorization_issuance_threshold)

    age_device(config, "phone-1", days=40)
    phone_trust = orch.compute_trust_score("phone-1")
    laptop_trust = orch.compute_trust_score("laptop-1")
    print(f"  phone-1 trust score (after 40 days in good standing): {phone_trust:.2f}")
    print(f"  laptop-1 trust score:                                 {laptop_trust:.2f}")
    check(f"aged, clean phone now clears the issuance threshold ({phone_trust:.2f} >= "
          f"{config.authorization_issuance_threshold})",
          phone_trust >= config.authorization_issuance_threshold)

    # ---- create a session for the laptop ----
    section("Create laptop session (ACTIVE)")
    laptop_session = orch.create_session("laptop-1")
    check("laptop session starts ACTIVE", orch.get_session_state(laptop_session) == NodeState.ACTIVE)

    # ---- LOCK: laptop self-quiesces (e.g. screen lock / idle) ----
    section("LOCK — laptop claims QUIESCE")
    orch.quiesce_session(laptop_session, {"cpu_load": 0.05, "memory_pressure": 0.10})
    check("laptop session is now QUIESCE", orch.get_session_state(laptop_session) == NodeState.QUIESCE)

    # ---- REVIVE: phone authorizes the laptop back to ACTIVE ----
    section("REVIVE — phone (as authorizer) signs a nonce to revive the laptop")
    nonce = orch.request_authorization_challenge(laptop_session)
    signature = phone_key.sign(nonce.encode())
    token = orch.peer_authorize("phone-1", laptop_session, nonce, signature)
    new_state = orch.revive_session(laptop_session, token)
    check("laptop session revived to ACTIVE using only the phone as authorizer",
          new_state == NodeState.ACTIVE)

    # ---- replay protection: the same nonce cannot mint a second Authorization ----
    section("Replay check — reusing the same nonce is rejected")
    replay_rejected = False
    try:
        orch.peer_authorize("phone-1", laptop_session, nonce, signature)
    except AuthorizationError:
        replay_rejected = True
    check("reused nonce is rejected (non-replayable Authorization)", replay_rejected)

    # ---- forged-signature protection ----
    section("Forged-signature check — a signature from the wrong key is rejected")
    attacker_key = SimulatedHardwareKey()
    orch.quiesce_session(laptop_session, {"cpu_load": 0.2})
    nonce2 = orch.request_authorization_challenge(laptop_session)
    forged_sig = attacker_key.sign(nonce2.encode())
    forged_sig_rejected = False
    try:
        orch.peer_authorize("phone-1", laptop_session, nonce2, forged_sig)
    except AuthorizationError:
        forged_sig_rejected = True
    check("signature from a non-enrolled key is rejected even with a valid nonce", forged_sig_rejected)
    orch.revive_session(laptop_session, orch.peer_authorize(
        "phone-1", laptop_session, nonce2, phone_key.sign(nonce2.encode())
    ))  # legitimately revive to reset state for the next section

    # ---- QUARANTINE via spoofed reauth ----
    section("QUARANTINE — a spoofed reauth attempt against the laptop session")
    forged_token = "this.is.not.a.valid.jwt"
    spoofed_rejected = False
    try:
        orch.revive_session(laptop_session, forged_token)
    except AuthorizationError:
        spoofed_rejected = True
    state_after_spoof = orch.get_session_state(laptop_session)
    check("spoofed reauth token is rejected", spoofed_rejected)
    check("spoofed reauth attempt reliably triggers QUARANTINE", state_after_spoof == NodeState.QUARANTINE)

    # ---- QUARANTINE recovery: soft tier ----
    section("Quarantine recovery — soft tier (same device, biometric + cooldown)")
    soft_ok = orch.recover_soft(laptop_session, is_enrolled_device=True,
                                impossible_travel=False, biometric_verified=True)
    check("soft recovery succeeds for an enrolled device with biometric confirmation", soft_ok)
    check("laptop session is ACTIVE again after soft recovery",
          orch.get_session_state(laptop_session) == NodeState.ACTIVE)

    biometric_required = orch.quarantine_session(laptop_session, reason="demo: re-quarantine for biometric test")
    no_biometric_result = orch.recover_soft(laptop_session, is_enrolled_device=True,
                                             impossible_travel=False, biometric_verified=False)
    check("soft recovery is refused without biometric confirmation", no_biometric_result is False)

    # ---- QUARANTINE recovery: hard tier required for unenrolled / impossible travel ----
    section("Quarantine recovery — hard tier required for unenrolled device / impossible travel")
    hard_tier_enforced = False
    try:
        orch.recover_soft(laptop_session, is_enrolled_device=False,
                           impossible_travel=False, biometric_verified=True)
    except QuarantineRecoveryError:
        hard_tier_enforced = True
    check("soft recovery is refused when the session classifies as hard-tier", hard_tier_enforced)

    wrong_key_rejected = not orch.recover_hard(laptop_session, "wrong-key-entirely")
    check("hard recovery rejects an incorrect offline recovery key", wrong_key_rejected)

    real_key = open(config.offline_recovery_key_path).read().strip()
    hard_recovered = orch.recover_hard(laptop_session, real_key)
    check("hard recovery succeeds with the correct offline recovery key", hard_recovered)
    check("laptop session is ACTIVE again after hard recovery",
          orch.get_session_state(laptop_session) == NodeState.ACTIVE)

    # ---- QUIESCE TTL expiry -> automatic QUARANTINE ----
    section("QUIESCE TTL expiry -> automatic QUARANTINE (no Authorization presented in time)")
    orch.quiesce_session(laptop_session, {"cpu_load": 0.9})
    time.sleep(config.quiesce_ttl_seconds + 0.5)
    expired = orch.sweep_expired_quiesce()
    check("expired QUIESCE session is swept into QUARANTINE automatically",
          laptop_session in expired and orch.get_session_state(laptop_session) == NodeState.QUARANTINE)
    orch.recover_hard(laptop_session, real_key)  # reset to ACTIVE for tidiness

    # ---- canary / decoy honeypot ----
    section("Canary — any touch is unambiguous evidence")
    orch.enroll_device("browser-1", DeviceRole.BROWSER)
    browser_session = orch.create_session("browser-1")
    canary_value = orch.plant_canary()
    triggered = orch.check_canary(canary_value, browser_session)
    check("touching the planted canary value triggers detection", triggered)
    check("the session that touched the canary is quarantined",
          orch.get_session_state(browser_session) == NodeState.QUARANTINE)
    not_triggered = orch.check_canary("some-unrelated-value", browser_session)
    check("presenting an unrelated value does not falsely trigger the canary", not_triggered is False)

    # ---- vault: short-lived, scoped tokens ----
    section("Vault — short-lived, scoped tokens instead of one master key")
    tok = orch.issue_token("laptop-1", scope="demo:capability", ttl_seconds=1)
    payload = orch.verify_token(tok)
    check("freshly issued token verifies with the correct scope", payload["scope"] == "demo:capability")
    time.sleep(1.3)
    expired_rejected = False
    try:
        orch.verify_token(tok)
    except VaultError:
        expired_rejected = True
    check("expired token is rejected", expired_rejected)

    tok2 = orch.issue_token("laptop-1", scope="demo:capability2", ttl_seconds=300)
    orch.vault.revoke_all_for_device("laptop-1")
    revoked_rejected = False
    try:
        orch.verify_token(tok2)
    except VaultError:
        revoked_rejected = True
    check("revoked token is rejected even before its TTL expires", revoked_rejected)

    # ---- ledger integrity ----
    section("Ledger — every transition logged, hash chain intact")
    ok, _ = orch.verify_ledger()
    check("hash chain is intact end-to-end before tampering", ok)
    total_entries = orch.ledger.count()
    print(f"  total ledger entries recorded so far: {total_entries}")

    conn = sqlite3.connect(config.db_path)
    conn.execute('UPDATE ledger SET payload = ? WHERE id = 1', ('{"tampered": true}',))
    conn.commit()
    conn.close()
    ok_after_tamper, bad_id = orch.verify_ledger()
    check(f"tampering with ledger row 1 is detected by verify_chain() (first broken id={bad_id})",
          not ok_after_tamper)

    # ---- summary ----
    section("SUMMARY")
    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    print(f"\n{passed}/{total} checks passed.")
    if passed == total:
        print("Phase 1 exit criteria: SATISFIED.")
    else:
        print("Phase 1 exit criteria: NOT satisfied — see failures above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
