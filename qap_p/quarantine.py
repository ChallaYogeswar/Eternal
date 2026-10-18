"""
Two-tier QUARANTINE recovery.

Eternal_Master_Build_Plan.md Section 6.1, "fix from design review":

    Soft tier: same enrolled device, new IP/geo, otherwise-normal behavior
               (e.g. you're travelling) -> biometric + a cooldown window.
    Hard tier: new/unenrolled device, or a geographically-implausible
               "impossible travel" jump -> offline physical recovery key
               only.

The classification inputs (is_enrolled_device, impossible_travel) are taken
as explicit booleans for Phase 1 rather than derived automatically — there's
no real session telemetry yet (IP geolocation history, travel-speed
modelling) to derive them from. Wiring this to genuine anomaly detection is
future work once the substrate here has real usage data to learn from.
"""
import hashlib
import time

from .ledger import Ledger
from .state_machine import DeviceStateMachine


class QuarantineRecoveryError(Exception):
    pass


class QuarantineRecovery:
    def __init__(self, state_machine: DeviceStateMachine, ledger: Ledger,
                 offline_recovery_key_hash: str, soft_cooldown_seconds: float = 300):
        self.state_machine = state_machine
        self.ledger = ledger
        self._offline_recovery_key_hash = offline_recovery_key_hash
        self.soft_cooldown_seconds = soft_cooldown_seconds
        self._last_soft_attempt: dict = {}

    @staticmethod
    def classify(is_enrolled_device: bool, impossible_travel: bool) -> str:
        if not is_enrolled_device or impossible_travel:
            return "hard"
        return "soft"

    def attempt_soft_recovery(self, session_id: str, is_enrolled_device: bool,
                               impossible_travel: bool, biometric_verified: bool) -> bool:
        tier = self.classify(is_enrolled_device, impossible_travel)
        if tier != "soft":
            self.ledger.append("soft_recovery_rejected", {
                "session_id": session_id,
                "reason": "classifies as hard-tier; soft recovery not permitted",
            })
            raise QuarantineRecoveryError(
                "this session classifies as hard-tier (unenrolled device or impossible "
                "travel); soft recovery is not permitted — use the offline recovery key"
            )

        if not biometric_verified:
            self.ledger.append("soft_recovery_failed", {
                "session_id": session_id, "reason": "biometric not verified",
            })
            return False

        last = self._last_soft_attempt.get(session_id, 0)
        elapsed = time.time() - last
        if elapsed < self.soft_cooldown_seconds:
            self.ledger.append("soft_recovery_blocked_cooldown", {
                "session_id": session_id, "remaining_seconds": self.soft_cooldown_seconds - elapsed,
            })
            return False

        self._last_soft_attempt[session_id] = time.time()
        self.state_machine.force_active(session_id, reason="soft recovery: biometric + cooldown satisfied")
        self.ledger.append("soft_recovery_succeeded", {"session_id": session_id})
        return True

    def attempt_hard_recovery(self, session_id: str, presented_key: str) -> bool:
        presented_hash = hashlib.sha256(presented_key.encode()).hexdigest()
        if presented_hash != self._offline_recovery_key_hash:
            self.ledger.append("hard_recovery_failed", {"session_id": session_id})
            return False
        self.state_machine.force_active(session_id, reason="hard recovery: offline recovery key presented")
        self.ledger.append("hard_recovery_succeeded", {"session_id": session_id})
        return True
