"""
Per-tool consent registry.

Eternal_Master_Build_Plan.md Section 5.2 (Differentiating features):
"Fine-grained per-tool consent instead of all-or-nothing." Phase 3's
ROTATE ring already answers "is this session currently allowed to invoke
this tool at all" (a short-lived, revocable permission token). Consent is
a different, longer-lived question: "has the owner actually agreed to let
Eternal write to this specific tracker module." A revoked ROTATE token
just means re-request one; a revoked consent means Eternal must not even
offer to write there again until the owner explicitly re-grants it.

Persisted to SQLite (survives a restart, unlike ROTATE's in-memory grants
— consent shouldn't have to be re-given every time the daemon restarts).
Every grant and revoke is ledger-logged, same audit posture as everything
else in this project.
"""
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

from .ledger_bridge import Ledger


@dataclass
class ConsentRecord:
    tool_name: str
    granted: bool
    granted_by: str
    granted_at: float
    note: str = ""


class ConsentRegistry:
    def __init__(self, db_path: str, ledger: Ledger):
        self.db_path = db_path
        self.ledger = ledger
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS consent (
                tool_name TEXT PRIMARY KEY,
                granted INTEGER NOT NULL,
                granted_by TEXT NOT NULL,
                granted_at REAL NOT NULL,
                note TEXT DEFAULT ''
            )
        """)
        conn.commit()
        conn.close()

    def grant(self, tool_name: str, granted_by: str, note: str = "") -> ConsentRecord:
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO consent (tool_name, granted, granted_by, granted_at, note) VALUES (?, 1, ?, ?, ?) "
                "ON CONFLICT(tool_name) DO UPDATE SET granted=1, granted_by=excluded.granted_by, "
                "granted_at=excluded.granted_at, note=excluded.note",
                (tool_name, granted_by, now, note),
            )
            conn.commit()
        finally:
            conn.close()
        self.ledger.append("consent_granted", {"tool_name": tool_name, "granted_by": granted_by, "note": note})
        return ConsentRecord(tool_name=tool_name, granted=True, granted_by=granted_by, granted_at=now, note=note)

    def revoke(self, tool_name: str, revoked_by: str, note: str = "") -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO consent (tool_name, granted, granted_by, granted_at, note) VALUES (?, 0, ?, ?, ?) "
                "ON CONFLICT(tool_name) DO UPDATE SET granted=0, granted_by=excluded.granted_by, "
                "granted_at=excluded.granted_at, note=excluded.note",
                (tool_name, revoked_by, time.time(), note),
            )
            conn.commit()
        finally:
            conn.close()
        self.ledger.append("consent_revoked", {"tool_name": tool_name, "revoked_by": revoked_by, "note": note})

    def has_consent(self, tool_name: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute("SELECT granted FROM consent WHERE tool_name = ?", (tool_name,)).fetchone()
        finally:
            conn.close()
        return bool(row and row[0])

    def get(self, tool_name: str) -> Optional[ConsentRecord]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT tool_name, granted, granted_by, granted_at, note FROM consent WHERE tool_name = ?",
                (tool_name,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return ConsentRecord(tool_name=row[0], granted=bool(row[1]), granted_by=row[2], granted_at=row[3], note=row[4])
