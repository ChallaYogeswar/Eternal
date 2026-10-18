"""
Device/session state machine — the core substrate Phase 1 exists to prove
out. QAP whitepaper Section 3.1-3.2.

Enterprise QAP enforces a 51%-ACTIVE quorum before granting QUIESCE
(Section 3.2, "Mutual Exclusion Constraint") to stop coordinated
denial-of-service via mass QUIESCE claims. At personal scale — a handful of
devices you own — that constraint doesn't defend against anything real:
there's no adversary who benefits from *your own* laptop and phone both
resting at once. It is intentionally dropped here rather than ported
blindly. If a future phase adds enough independently-untrusted nodes that
the idea starts to matter again, this is the place to reintroduce it.
"""
import time

from .models import NodeState, Session
from .store import Store
from .ledger import Ledger


class StateMachineError(Exception):
    pass


class DeviceStateMachine:
    def __init__(self, store: Store, ledger: Ledger, quiesce_ttl_seconds: float = 30):
        self.store = store
        self.ledger = ledger
        self.quiesce_ttl_seconds = quiesce_ttl_seconds

    def create_session(self, session_id: str, device_id: str) -> Session:
        now = time.time()
        session = Session(
            session_id=session_id, device_id=device_id, state=NodeState.ACTIVE,
            created_at=now, last_transition_at=now,
        )
        self.store.create_session(session)
        self.ledger.append("session_created", {"session_id": session_id, "device_id": device_id})
        return session

    def claim_quiesce(self, session_id: str, load_certificate: dict) -> Session:
        """QAP Section 3.2: a node submits a (here: simulated) hardware-
        attested load certificate to enter the protected rest state."""
        session = self.store.get_session(session_id)
        if session is None:
            raise StateMachineError("unknown session")
        if session.state != NodeState.ACTIVE:
            raise StateMachineError(f"cannot claim QUIESCE from state {session.state.value}")
        expires_at = time.time() + self.quiesce_ttl_seconds
        self.store.update_session_state(session_id, NodeState.QUIESCE, quiesce_expires_at=expires_at)
        self.ledger.append("quiesce_claimed", {
            "session_id": session_id, "load_certificate": load_certificate, "expires_at": expires_at,
        })
        return self.store.get_session(session_id)

    def sweep_expired_quiesce(self) -> list:
        """QAP Section 3.2: 'A node that remains in QUIESCE beyond its TTL
        without receiving a valid Authorization is automatically
        transitioned to QUARANTINE.' Call this periodically — Phase 1 runs
        it from a background thread in the daemon (see api.py); on the real
        Pi deployment a systemd timer works just as well."""
        expired = []
        for session in self.store.list_sessions_in_state(NodeState.QUIESCE):
            if session.quiesce_expires_at and time.time() > session.quiesce_expires_at:
                self.quarantine(session.session_id, reason="QUIESCE TTL exceeded without valid Authorization")
                expired.append(session.session_id)
        return expired

    def quarantine(self, session_id: str, reason: str) -> Session:
        session = self.store.get_session(session_id)
        if session is None:
            raise StateMachineError("unknown session")
        already_quarantined = session.state == NodeState.QUARANTINE
        self.store.update_session_state(session_id, NodeState.QUARANTINE, quarantine_reason=reason)
        if not already_quarantined:
            self.store.increment_quarantine_count(session.device_id)
        self.ledger.append("quarantine_triggered", {
            "session_id": session_id, "device_id": session.device_id, "reason": reason,
            "already_quarantined": already_quarantined,
        })
        return self.store.get_session(session_id)

    def force_active(self, session_id: str, reason: str) -> Session:
        """Used only by successful Authorization redemption or successful
        quarantine recovery — never exposed as a bare 'set active' primitive
        without a caller-supplied reason, since every transition here has to
        land in the ledger with an explanation."""
        session = self.store.get_session(session_id)
        if session is None:
            raise StateMachineError("unknown session")
        self.store.update_session_state(session_id, NodeState.ACTIVE)
        self.ledger.append("session_activated", {"session_id": session_id, "reason": reason})
        return self.store.get_session(session_id)
