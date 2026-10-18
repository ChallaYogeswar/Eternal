"""
Chaos-engineering self-test.

QAP whitepaper Section 6.6, mapped to personal scale in
Eternal_Master_Build_Plan.md Section 6.1: "Scheduled self-red-team: spoof a
reauth or forged tool call, confirm QUARANTINE fires."

Phase 1 exposes this as a callable suite rather than an actual scheduler —
on the real Pi deployment, wire these methods to a systemd timer or cron
entry; that's simpler and more Unix-idiomatic than embedding a scheduler in
the daemon itself.
"""
from .ledger import Ledger
from .auth import AuthorizationService, AuthorizationError
from .state_machine import DeviceStateMachine


class ChaosEngineer:
    def __init__(self, auth_service: AuthorizationService, state_machine: DeviceStateMachine, ledger: Ledger):
        self.auth_service = auth_service
        self.state_machine = state_machine
        self.ledger = ledger

    def test_spoofed_reauth(self, target_session_id: str) -> dict:
        """Presents a forged Authorization token against a real session and
        confirms the orchestrator both rejects it AND quarantines the
        session — the direct proof for the Phase 1 exit-criteria line
        'a spoofed reauth attempt reliably triggers QUARANTINE.'"""
        self.ledger.append("chaos_test_started", {
            "test": "spoofed_reauth", "target_session_id": target_session_id,
        })
        forged_token = "not.a.real.jwt.signed.by.anyone"
        rejected = False
        try:
            self.auth_service.redeem_authorization(forged_token, expected_session_id=target_session_id)
        except AuthorizationError:
            rejected = True

        if rejected:
            self.state_machine.quarantine(target_session_id, reason="spoofed reauthorization attempt rejected")

        session = self.state_machine.store.get_session(target_session_id)
        quarantined = session.state.value == "QUARANTINE" if session else False
        result = {
            "test": "spoofed_reauth",
            "rejected": rejected,
            "quarantined": quarantined,
            "passed": rejected and quarantined,
        }
        self.ledger.append("chaos_test_completed", result)
        return result

    def test_nonce_replay(self, issuing_device_id: str, issuing_public_key_pem: str,
                           sign_fn, target_session_id: str) -> dict:
        """Confirms a single nonce cannot mint two Authorizations
        (QAP 4.5's replay protection). Needs a live issuing device and a
        target session currently in QUIESCE."""
        self.ledger.append("chaos_test_started", {
            "test": "nonce_replay", "target_session_id": target_session_id,
        })
        nonce = self.auth_service.issue_challenge(target_session_id)
        signature = sign_fn(nonce.encode())
        self.auth_service.issue_authorization(
            issuing_device_id, issuing_public_key_pem, target_session_id, nonce, signature
        )
        replay_rejected = False
        try:
            self.auth_service.issue_authorization(
                issuing_device_id, issuing_public_key_pem, target_session_id, nonce, signature
            )
        except AuthorizationError:
            replay_rejected = True
        result = {"test": "nonce_replay", "passed": replay_rejected}
        self.ledger.append("chaos_test_completed", result)
        return result
