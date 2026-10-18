"""
Hash-chained, append-only ledger.

QAP whitepaper Section 6.3 ("QUIESCE History Ledger") mapped to personal
scale in Eternal_Master_Build_Plan.md Section 6.1: "Hash-chained, append-only
SQLite log of every memory write, tool call, and external request — this
*is* the audit trail referenced everywhere else in this doc."

Every entry links to the hash of the one before it, so any edit or deletion
anywhere in the history breaks the chain from that point forward.
verify_chain() is the standing proof that hasn't happened.
"""
import sqlite3
import hashlib
import json
import time
from typing import Optional, Tuple, List

GENESIS_HASH = "0" * 64


class Ledger:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def _last_hash(self, conn) -> str:
        cur = conn.execute("SELECT hash FROM ledger ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else GENESIS_HASH

    @staticmethod
    def _compute_hash(prev_hash: str, timestamp: float, event_type: str, payload_json: str) -> str:
        to_hash = f"{prev_hash}|{timestamp!r}|{event_type}|{payload_json}"
        return hashlib.sha256(to_hash.encode()).hexdigest()

    def append(self, event_type: str, payload: dict) -> str:
        conn = self._connect()
        try:
            prev_hash = self._last_hash(conn)
            timestamp = time.time()
            payload_json = json.dumps(payload, sort_keys=True, default=str)
            entry_hash = self._compute_hash(prev_hash, timestamp, event_type, payload_json)
            conn.execute(
                "INSERT INTO ledger (timestamp, event_type, payload, prev_hash, hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (timestamp, event_type, payload_json, prev_hash, entry_hash),
            )
            conn.commit()
            return entry_hash
        finally:
            conn.close()

    def verify_chain(self) -> Tuple[bool, Optional[int]]:
        """Returns (True, None) if the whole chain is intact, else
        (False, id_of_first_broken_entry)."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT id, timestamp, event_type, payload, prev_hash, hash "
                "FROM ledger ORDER BY id ASC"
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        prev_hash = GENESIS_HASH
        for row in rows:
            entry_id, timestamp, event_type, payload_json, stored_prev_hash, stored_hash = row
            if stored_prev_hash != prev_hash:
                return False, entry_id
            recomputed = self._compute_hash(stored_prev_hash, timestamp, event_type, payload_json)
            if recomputed != stored_hash:
                return False, entry_id
            prev_hash = stored_hash
        return True, None

    def recent(self, limit: int = 100) -> List[dict]:
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT id, timestamp, event_type, payload, hash FROM ledger "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        entries = [
            {"id": r[0], "timestamp": r[1], "event_type": r[2], "payload": json.loads(r[3]), "hash": r[4]}
            for r in rows
        ]
        return entries[::-1]

    def count(self) -> int:
        conn = self._connect()
        try:
            cur = conn.execute("SELECT COUNT(*) FROM ledger")
            return cur.fetchone()[0]
        finally:
            conn.close()
