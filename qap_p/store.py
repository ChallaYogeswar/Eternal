"""
SQLite-backed persistence for devices, sessions, and vault tokens.

Personal-scale stand-in for what would be a proper clustered database in an
enterprise QAP deployment. One file, one process, no cluster — deliberate,
per Eternal_Master_Build_Plan.md's "one lightweight daemon on the Pi" framing
(Sections 3 and 6.1), rather than QAP's 3-node Raft orchestrator.
"""
import sqlite3
import time
from typing import Optional, List

from .models import Device, Session, NodeState, DeviceRole


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        conn = self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                public_key_pem TEXT NOT NULL,
                hardware_backed INTEGER NOT NULL,
                attestation_type TEXT NOT NULL,
                enrolled_at REAL NOT NULL,
                quarantine_count INTEGER NOT NULL DEFAULT 0,
                anomaly_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_transition_at REAL NOT NULL,
                quiesce_expires_at REAL,
                quarantine_reason TEXT,
                FOREIGN KEY (device_id) REFERENCES devices(device_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                token_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                issued_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                revoked_at REAL
            )
        """)
        conn.commit()
        conn.close()

    # ---- devices ----
    def create_device(self, device: Device) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT INTO devices (device_id, role, public_key_pem, hardware_backed, "
            "attestation_type, enrolled_at, quarantine_count, anomaly_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (device.device_id, device.role.value, device.public_key_pem,
             int(device.hardware_backed), device.attestation_type, device.enrolled_at,
             device.quarantine_count, device.anomaly_count),
        )
        conn.commit()
        conn.close()

    def get_device(self, device_id: str) -> Optional[Device]:
        conn = self._connect()
        cur = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,))
        row = cur.fetchone()
        conn.close()
        if row is None:
            return None
        return Device(
            device_id=row[0], role=DeviceRole(row[1]), public_key_pem=row[2],
            hardware_backed=bool(row[3]), attestation_type=row[4], enrolled_at=row[5],
            quarantine_count=row[6], anomaly_count=row[7],
        )

    def list_devices(self) -> List[Device]:
        conn = self._connect()
        cur = conn.execute("SELECT * FROM devices")
        rows = cur.fetchall()
        conn.close()
        return [
            Device(device_id=r[0], role=DeviceRole(r[1]), public_key_pem=r[2],
                   hardware_backed=bool(r[3]), attestation_type=r[4], enrolled_at=r[5],
                   quarantine_count=r[6], anomaly_count=r[7])
            for r in rows
        ]

    def increment_quarantine_count(self, device_id: str) -> None:
        conn = self._connect()
        conn.execute("UPDATE devices SET quarantine_count = quarantine_count + 1 WHERE device_id = ?", (device_id,))
        conn.commit()
        conn.close()

    def increment_anomaly_count(self, device_id: str) -> None:
        conn = self._connect()
        conn.execute("UPDATE devices SET anomaly_count = anomaly_count + 1 WHERE device_id = ?", (device_id,))
        conn.commit()
        conn.close()

    # ---- sessions ----
    def create_session(self, session: Session) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT INTO sessions (session_id, device_id, state, created_at, "
            "last_transition_at, quiesce_expires_at, quarantine_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session.session_id, session.device_id, session.state.value, session.created_at,
             session.last_transition_at, session.quiesce_expires_at, session.quarantine_reason),
        )
        conn.commit()
        conn.close()

    def get_session(self, session_id: str) -> Optional[Session]:
        conn = self._connect()
        cur = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        row = cur.fetchone()
        conn.close()
        if row is None:
            return None
        return Session(
            session_id=row[0], device_id=row[1], state=NodeState(row[2]), created_at=row[3],
            last_transition_at=row[4], quiesce_expires_at=row[5], quarantine_reason=row[6],
        )

    def update_session_state(self, session_id: str, state: NodeState,
                              quiesce_expires_at: Optional[float] = None,
                              quarantine_reason: Optional[str] = None) -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE sessions SET state = ?, last_transition_at = ?, "
            "quiesce_expires_at = ?, quarantine_reason = ? WHERE session_id = ?",
            (state.value, time.time(), quiesce_expires_at, quarantine_reason, session_id),
        )
        conn.commit()
        conn.close()

    def list_sessions_for_device(self, device_id: str) -> List[Session]:
        conn = self._connect()
        cur = conn.execute("SELECT * FROM sessions WHERE device_id = ?", (device_id,))
        rows = cur.fetchall()
        conn.close()
        return [
            Session(session_id=r[0], device_id=r[1], state=NodeState(r[2]), created_at=r[3],
                    last_transition_at=r[4], quiesce_expires_at=r[5], quarantine_reason=r[6])
            for r in rows
        ]

    def list_sessions_in_state(self, state: NodeState) -> List[Session]:
        conn = self._connect()
        cur = conn.execute("SELECT * FROM sessions WHERE state = ?", (state.value,))
        rows = cur.fetchall()
        conn.close()
        return [
            Session(session_id=r[0], device_id=r[1], state=NodeState(r[2]), created_at=r[3],
                    last_transition_at=r[4], quiesce_expires_at=r[5], quarantine_reason=r[6])
            for r in rows
        ]

    # ---- tokens ----
    def create_token_record(self, token_id: str, device_id: str, scope: str,
                             issued_at: float, expires_at: float) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT INTO tokens (token_id, device_id, scope, issued_at, expires_at, revoked_at) "
            "VALUES (?, ?, ?, ?, ?, NULL)",
            (token_id, device_id, scope, issued_at, expires_at),
        )
        conn.commit()
        conn.close()

    def is_token_revoked(self, token_id: str) -> bool:
        conn = self._connect()
        cur = conn.execute("SELECT revoked_at FROM tokens WHERE token_id = ?", (token_id,))
        row = cur.fetchone()
        conn.close()
        if row is None:
            return True  # unknown token id — treat as invalid/revoked
        return row[0] is not None

    def revoke_token(self, token_id: str) -> None:
        conn = self._connect()
        conn.execute("UPDATE tokens SET revoked_at = ? WHERE token_id = ?", (time.time(), token_id))
        conn.commit()
        conn.close()

    def revoke_all_tokens_for_device(self, device_id: str) -> int:
        conn = self._connect()
        cur = conn.execute(
            "UPDATE tokens SET revoked_at = ? WHERE device_id = ? AND revoked_at IS NULL",
            (time.time(), device_id),
        )
        conn.commit()
        count = cur.rowcount
        conn.close()
        return count
