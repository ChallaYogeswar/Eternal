"""
Decoy honeypot.

QAP whitepaper Section 6.2, mapped to personal scale in
Eternal_Master_Build_Plan.md Section 6.1: "Canary credential planted in
config/notes — any session touching it auto-quarantines."

The canary value itself is never logged verbatim anywhere a real credential
would be logged — only whether it was touched, by which session, and when.
"""
import secrets

from .ledger import Ledger
from .state_machine import DeviceStateMachine


class CanaryService:
    def __init__(self, ledger: Ledger, state_machine: DeviceStateMachine, vault=None):
        self.ledger = ledger
        self.state_machine = state_machine
        self.vault = vault
        self._value = None

    def plant(self) -> str:
        self._value = f"canary-{secrets.token_hex(16)}"
        self.ledger.append("canary_planted", {})
        return self._value

    def check_access(self, presented_value: str, accessor_session_id: str) -> bool:
        """Returns True (and quarantines the accessing session + revokes its
        device's tokens) if the presented value matches the planted canary.
        Any match is unambiguous — nothing legitimate should ever read this
        value, so there's no "maybe" here."""
        if self._value is not None and presented_value == self._value:
            session = self.state_machine.store.get_session(accessor_session_id)
            self.state_machine.quarantine(accessor_session_id, reason="canary credential accessed")
            if self.vault is not None and session is not None:
                self.vault.revoke_all_for_device(session.device_id)
            self.ledger.append("canary_triggered", {"accessor_session_id": accessor_session_id})
            return True
        return False
