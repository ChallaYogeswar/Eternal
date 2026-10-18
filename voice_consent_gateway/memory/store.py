"""
Structured long-term memory store.

eternal_core/rag/store.py (Phase 2) is explicit that it only implements
the semantic/vector half of Eternal's memory (ChromaDB) and defers the
structured half: "the SQLite half, structured long-term facts, is Phase
4+ territory." This module is that territory. A "fact" here is a small,
discrete, structured claim ("user's dog is named Max," "user prefers
metric units") — the kind of thing Claude's own memory system stores,
scaled down to Eternal's personal-assistant use case — as distinct from
the free-text conversational chunks ChromaDB indexes for semantic recall.

This module only knows how to persist a fact once it's already been
cleared. It has no gating logic of its own — see gated_writer.py, which
is the only intended caller of `write_fact` below in a real deployment.
Kept as a separate module anyway (rather than folding storage into the
gate) because "how facts are stored" and "whether a fact is safe to
store" are different concerns, same separation rcap_p keeps between
inspect_ring.py (decides) and everything that acts on its verdict.
"""
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional

from ..models import MemoryCategory


@dataclass
class Fact:
    id: str
    key: str
    value: str
    category: str
    source: str
    created_at: float


class MemoryStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id TEXT PRIMARY KEY,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                category TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def write_fact(self, key: str, value: str, category: MemoryCategory, source: str) -> Fact:
        fact = Fact(id=str(uuid.uuid4()), key=key, value=value, category=category.value,
                    source=source, created_at=time.time())
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO facts (id, key, value, category, source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (fact.id, fact.key, fact.value, fact.category, fact.source, fact.created_at),
            )
            conn.commit()
        finally:
            conn.close()
        return fact

    def get_fact(self, key: str) -> Optional[Fact]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, key, value, category, source, created_at FROM facts "
                "WHERE key = ? ORDER BY created_at DESC LIMIT 1",
                (key,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return Fact(id=row[0], key=row[1], value=row[2], category=row[3], source=row[4], created_at=row[5])

    def list_facts(self, category: Optional[MemoryCategory] = None) -> List[Fact]:
        conn = self._connect()
        try:
            if category:
                rows = conn.execute(
                    "SELECT id, key, value, category, source, created_at FROM facts "
                    "WHERE category = ? ORDER BY created_at DESC",
                    (category.value,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, key, value, category, source, created_at FROM facts ORDER BY created_at DESC"
                ).fetchall()
        finally:
            conn.close()
        return [Fact(id=r[0], key=r[1], value=r[2], category=r[3], source=r[4], created_at=r[5]) for r in rows]

    def count(self) -> int:
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        finally:
            conn.close()
