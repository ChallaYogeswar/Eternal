"""
QAP-P Orchestrator — the single daemon process Eternal_Master_Build_Plan.md
Section 3 describes as "one lightweight daemon on the Raspberry Pi
(identity/session/ledger only)". This class is the whole substrate's public
surface; the FastAPI app in api.py is a thin HTTP wrapper around it, and
scripts/qap_cli.py and scripts/demo_exit_criteria.py drive it directly for
local testing.
"""
import hashlib
import secrets
import time
import uuid
from typing import Any, Optional

from .config import Config
from .models import Device, DeviceRole, NodeState
from .store import Store
from .ledger import Ledger
from .hardware_key import HardwareKeyProvider, SimulatedHardwareKey
from .attestation import AttestationVerifier, FailClosedVerifier
from .trust import TrustFactors, compute_trust_score, can_issue_authorization
from .vault import Vault
from .auth import AuthorizationService, AuthorizationError
from .state_machine import DeviceStateMachine
from .canary import CanaryService
from .quarantine import QuarantineRecovery
from .chaos import ChaosEngineer


class Orchestrator:
    def __init__(self, config: Optional[Config] = None, attestation_verifier: Optional[AttestationVerifier] = None):
        self.config = config or Config()
        # Audit E-02: `hardware_backed` is decided by this verifier ONLY. Default is fail-closed.
        self.attestation_verifier = attestation_verifier or FailClosedVerifier()
        self.store = Store(self.config.db_path)
        self.ledger = Ledger(self.config.db_path)
        self.vault = Vault(self.config.vault_key_path, self.store, self.ledger)
        self.state_machine = DeviceStateMachine(self.store, self.ledger, self.config.quiesce_ttl_seconds)
        self.canary = CanaryService(self.ledger, self.state_machine, self.vault)

        self._orchestrator_secret = self._load_or_create_secret()
        self.auth_service = AuthorizationService(
            self._orchestrator_secret, self.ledger, self.config.authorization_ttl_seconds
        )
        self.chaos = ChaosEngineer(self.auth_service, self.state_machine, self.ledger)

        offline_key_hash = self._load_or_create_offline_recovery_key()
        self.quarantine_recovery = QuarantineRecovery(
            self.state_machine, self.ledger, offline_key_hash, self.config.soft_recovery_cooldown_seconds
        )

    # ---- bootstrap secrets ----
    def _load_or_create_secret(self) -> str:
        import os
        path = self.config.db_path + ".orchestrator_secret"
        if os.path.exists(path):
            return open(path).read().strip()
        secret = secrets.token_hex(32)
        with open(path, "w") as f:
            f.write(secret)
        os.chmod(path, 0o600)
        return secret

    def _load_or_create_offline_recovery_key(self) -> str:
        """The 'offline, physically-stored recovery key' from build plan
        Section 6.1. Phase 1 generates one on first run and prints it once —
        in real use you'd write this down / print it and store it away from
        every enrolled device, precisely so a compromise of any one device
        doesn't also hand over the recovery path."""
        import os
        path = self.config.offline_recovery_key_path
        if os.path.exists(path):
            key = open(path).read().strip()
        else:
            key = secrets.token_hex(24)
            with open(path, "w") as f:
                f.write(key)
            os.chmod(path, 0o600)
            print(f"[QAP-P] Generated offline recovery key — store this OFFLINE, away from "
                  f"all enrolled devices: {key}")
        return hashlib.sha256(key.encode()).hexdigest()

    # ---- device enrollment ----
    def enroll_device(self, device_id: str, role: DeviceRole,
                       key_provider: Optional[HardwareKeyProvider] = None,
                       attestation_evidence: Optional[Any] = None) -> Device:
        key_provider = key_provider or SimulatedHardwareKey()
        pem = key_provider.public_key_pem()
        verdict = self.attestation_verifier.verify(
            device_id=device_id, public_key_pem=pem, attestation_type=key_provider.attestation_type,
            evidence=attestation_evidence)
        if self.config.require_verified_attestation and not verdict.verified:
            self.ledger.append("device_enrollment_refused", {
                "device_id": device_id, "role": role.value, "reason": verdict.detail,
                "attestation_verifier": self.attestation_verifier.name})
            raise ValueError(f"enrollment refused: attestation not verified ({verdict.detail})")
        device = Device(
            device_id=device_id,
            role=role,
            public_key_pem=pem,
            hardware_backed=bool(verdict.verified),
            attestation_type=key_provider.attestation_type,
            enrolled_at=time.time(),
        )
        self.store.create_device(device)
        self.ledger.append("device_enrolled", {
            "device_id": device_id, "role": role.value, "attestation_type": device.attestation_type,
            "hardware_backed": device.hardware_backed, "attestation_detail": verdict.detail,
            "attestation_verifier": self.attestation_verifier.name,
        })
        return device

    def get_device(self, device_id: str) -> Optional[Device]:
        return self.store.get_device(device_id)

    # ---- trust ----
    def compute_trust_score(self, device_id: str) -> float:
        device = self.store.get_device(device_id)
        if device is None:
            raise ValueError("unknown device")
        enrollment_age_days = (time.time() - device.enrolled_at) / 86400.0
        recent_quiesce = 0  # Phase 1: no windowed counting yet; see trust.py docstring
        good_standing_days = enrollment_age_days if device.quarantine_count == 0 else 0.0
        factors = TrustFactors(
            enrollment_age_days=enrollment_age_days,
            quarantine_count=device.quarantine_count,
            anomaly_count=device.anomaly_count,
            hardware_backed=device.hardware_backed,
            recent_quiesce_claims=recent_quiesce,
            good_standing_days=good_standing_days,
        )
        return compute_trust_score(factors)

    # ---- sessions ----
    def create_session(self, device_id: str) -> str:
        if self.store.get_device(device_id) is None:
            raise ValueError("device must be enrolled before creating a session")
        session_id = str(uuid.uuid4())
        self.state_machine.create_session(session_id, device_id)
        return session_id

    def get_session_state(self, session_id: str) -> NodeState:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError("unknown session")
        return session.state

    def quiesce_session(self, session_id: str, load_certificate: dict) -> NodeState:
        session = self.state_machine.claim_quiesce(session_id, load_certificate)
        return session.state

    def sweep_expired_quiesce(self) -> list:
        return self.state_machine.sweep_expired_quiesce()

    def quarantine_session(self, session_id: str, reason: str) -> NodeState:
        session = self.state_machine.quarantine(session_id, reason)
        return session.state

    # ---- peer authorization / revive ----
    def request_authorization_challenge(self, target_session_id: str) -> str:
        if self.store.get_session(target_session_id) is None:
            raise ValueError("unknown target session")
        return self.auth_service.issue_challenge(target_session_id)

    def peer_authorize(self, issuing_device_id: str, target_session_id: str,
                        nonce: str, signature: bytes) -> str:
        issuing_device = self.store.get_device(issuing_device_id)
        if issuing_device is None:
            raise ValueError("issuing device is not enrolled")
        score = self.compute_trust_score(issuing_device_id)
        if not can_issue_authorization(score, self.config.authorization_issuance_threshold):
            self.ledger.append("authorization_denied_low_trust", {
                "issuing_device_id": issuing_device_id, "trust_score": score,
            })
            raise AuthorizationError(
                f"issuing device trust score {score:.2f} is below the issuance threshold "
                f"{self.config.authorization_issuance_threshold:.2f} — cannot authorize peers yet"
            )
        return self.auth_service.issue_authorization(
            issuing_device_id, issuing_device.public_key_pem, target_session_id, nonce, signature
        )

    def revive_session(self, session_id: str, authorization_token: str) -> NodeState:
        """Redeem an Authorization to bring a session back to ACTIVE. Any
        verification failure — forged token, expired token, wrong session —
        is treated as a spoofed reauth attempt and quarantines the session.
        This is what makes the Phase 1 exit criteria's 'a spoofed reauth
        attempt reliably triggers QUARANTINE' true by construction, not by
        convention."""
        try:
            self.auth_service.redeem_authorization(authorization_token, expected_session_id=session_id)
        except AuthorizationError as e:
            self.state_machine.quarantine(session_id, reason=f"spoofed reauthorization attempt rejected: {e}")
            raise
        session = self.state_machine.force_active(session_id, reason="revived via peer Authorization")
        return session.state

    # ---- quarantine recovery ----
    def recover_soft(self, session_id: str, is_enrolled_device: bool,
                      impossible_travel: bool, biometric_verified: bool) -> bool:
        return self.quarantine_recovery.attempt_soft_recovery(
            session_id, is_enrolled_device, impossible_travel, biometric_verified
        )

    def recover_hard(self, session_id: str, presented_key: str) -> bool:
        return self.quarantine_recovery.attempt_hard_recovery(session_id, presented_key)

    # ---- vault ----
    def issue_token(self, device_id: str, scope: str, ttl_seconds: Optional[float] = None) -> str:
        ttl = ttl_seconds if ttl_seconds is not None else self.config.default_token_ttl_seconds
        return self.vault.issue_token(device_id, scope, ttl)

    def verify_token(self, token: str) -> dict:
        return self.vault.verify_token(token)

    # ---- canary ----
    def plant_canary(self) -> str:
        return self.canary.plant()

    def check_canary(self, presented_value: str, accessor_session_id: str) -> bool:
        return self.canary.check_access(presented_value, accessor_session_id)

    # ---- chaos engineering ----
    def run_chaos_suite(self, target_session_id: str) -> list:
        return [self.chaos.test_spoofed_reauth(target_session_id)]

    # ---- ledger ----
    def verify_ledger(self):
        return self.ledger.verify_chain()

    def ledger_entries(self, limit: int = 100):
        return self.ledger.recent(limit)
