"""
Run ledger: a thin, single-writer, hash-chained wrapper over eternal_core's existing Ledger.

Writes are serialised with a lock so concurrent runs get a total order. Payloads carry previews and
hashes, never full observations. The external signed checkpoint (head hash + count, key outside the
runtime) is O0b; this module only exposes `head()` for it to sign.
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

    def head(self) -> Tuple[str, int]:
        recent = self._ledger.recent(1)
        return (recent[-1]["hash"] if recent else "0" * 64), self._ledger.count()
