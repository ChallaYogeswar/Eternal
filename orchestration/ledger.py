"""
Run ledger: a thin, single-writer, hash-chained wrapper over eternal_core's existing Ledger.
Signed external checkpoints over `head()` live in `checkpoint.py`.

Writes are serialised with a lock so concurrent runs get a total order. Payloads carry previews and
hashes, never full observations. `head()` and `hash_at()` feed the signed external checkpoint.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional, Tuple

from eternal_core.audit.ledger import Ledger


class RunLedger:
    def __init__(self, db_path: str):
        self._ledger = Ledger(db_path)
        self._lock = threading.Lock()

    def append(self, event_type: str, payload: Dict[str, Any]) -> str:
        with self._lock:
            return self._ledger.append(event_type, payload)

    def verify_chain(self) -> Tuple[bool, Optional[int]]:
        return self._ledger.verify_chain()

    def count(self) -> int:
        return self._ledger.count()

    def recent(self, limit: int = 100):
        return self._ledger.recent(limit)

    def hash_at(self, position: int) -> Optional[str]:
        """Hash of the entry at 1-based POSITION (not row id: ids can have gaps if rows were deleted)."""
        if position < 1:
            return None
        conn = self._ledger._connect()
        try:
            row = conn.execute("SELECT hash FROM ledger ORDER BY id ASC LIMIT 1 OFFSET ?", (position - 1,)).fetchone()
        finally:
            conn.close()
        return row[0] if row else None

    def head(self) -> Tuple[str, int]:
        """(head hash, entry count) read atomically with respect to appends, so a checkpoint never pairs a head
        with a count from a different moment."""
        with self._lock:
            recent = self._ledger.recent(1)
            return (recent[-1]["hash"] if recent else "0" * 64), self._ledger.count()
