"""
CinemaVault data layer.

Honest scoping note (Eternal_Master_Build_Plan.md Section 2 and Section 9):
CinemaVault is named as "a personal tool for tracking movies/media... not
yet reviewed in depth — needs its actual interface reviewed before
MCP-wrapping in Phase 3," and Section 9 lists "CinemaVault's... actual
internal architecture — not yet reviewed" as a still-open item. There is
no separate CinemaVault codebase anywhere in the project's source
material (confirmed by inspecting TRACKER.zip directly: no cinemavault/
directory exists anywhere in it) — the closest real, reviewable interface
to "tracking movies/media" is ÆTHER's own Activity model filtered to
type=MOVIE (schema.prisma; backend/src/routes/activities.ts), which is
exactly the field shape this module mirrors: title, a metadata JSON blob
holding `where` (theatre/streaming platform) and `rating`, and
`performedAt` as the watch date.

This module wraps that real, reviewed shape — not a placeholder shape
invented for Phase 3 — behind a small local SQLite mirror, seeded with
sample data, so CinemaVault's read-only MCP server has something real to
serve without standing up ÆTHER's full Postgres+Prisma+Express stack just
to prove RCAP-P's gating logic (which is what Phase 3 is actually here to
prove). Wiring this MCP server at the real ÆTHER Postgres database instead
of this local mirror is a change local to this one file — server.py only
calls the functions below, never touches SQL directly — but doing that
wiring for real is still gated on the Section 9 item above: confirm
CinemaVault's actual, intended architecture (is it ÆTHER's Activities
module for real, or a genuinely separate tool?) before pointing a live
credentialed connection at production data.
"""
import os
import sqlite3
from typing import List, Optional, TypedDict

DB_PATH = os.environ.get("CINEMAVAULT_DB_PATH", os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "cinemavault.db"
))

_SEED_MOVIES = [
    # (id, title, watched_where, watched_at ISO, rating 1-10)
    ("m1", "Interstellar", "Home - Amazon", "2026-01-04T20:30:00", 9),
    ("m2", "Oppenheimer", "Theatre", "2026-01-18T19:00:00", 9),
    ("m3", "Spirited Away", "Home - Netflix", "2026-02-02T21:15:00", 8),
    ("m4", "The Dark Knight", "Home - Disney+", "2026-02-14T22:00:00", 10),
    ("m5", "Dune: Part Two", "Theatre", "2026-03-01T18:30:00", 9),
    ("m6", "Parasite", "Home - Netflix", "2026-03-20T21:00:00", 9),
    ("m7", "RRR", "Home - Amazon", "2026-04-05T20:00:00", 8),
    ("m8", "The Grand Budapest Hotel", "Home - Netflix", "2026-04-22T19:45:00", 8),
]


class MovieRow(TypedDict):
    id: str
    title: str
    watched_where: str
    watched_at: str
    rating: Optional[int]


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_and_seed(force: bool = False) -> None:
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS movies (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            watched_where TEXT NOT NULL,
            watched_at TEXT NOT NULL,
            rating INTEGER
        )
    """)
    count = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    if force or count == 0:
        if force:
            conn.execute("DELETE FROM movies")
        conn.executemany(
            "INSERT OR REPLACE INTO movies (id, title, watched_where, watched_at, rating) "
            "VALUES (?, ?, ?, ?, ?)",
            _SEED_MOVIES,
        )
    conn.commit()
    conn.close()


def list_movies(limit: int = 50, offset: int = 0) -> List[MovieRow]:
    conn = _connect()
    rows = conn.execute(
        "SELECT id, title, watched_where, watched_at, rating FROM movies "
        "ORDER BY watched_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    conn.close()
    return [
        MovieRow(id=r[0], title=r[1], watched_where=r[2], watched_at=r[3], rating=r[4])
        for r in rows
    ]


def get_movie(movie_id: str) -> Optional[MovieRow]:
    conn = _connect()
    row = conn.execute(
        "SELECT id, title, watched_where, watched_at, rating FROM movies WHERE id = ?",
        (movie_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return MovieRow(id=row[0], title=row[1], watched_where=row[2], watched_at=row[3], rating=row[4])


def search_movies(title_substring: str) -> List[MovieRow]:
    conn = _connect()
    rows = conn.execute(
        "SELECT id, title, watched_where, watched_at, rating FROM movies "
        "WHERE title LIKE ? ORDER BY watched_at DESC",
        (f"%{title_substring}%",),
    ).fetchall()
    conn.close()
    return [
        MovieRow(id=r[0], title=r[1], watched_where=r[2], watched_at=r[3], rating=r[4])
        for r in rows
    ]


def _insert_row_for_test(row: MovieRow) -> None:
    """Test-only helper (tests/test_mcp_servers.py) to insert a
    deliberately poisoned record — e.g. a title containing an injected
    instruction — without hand-writing SQL in the test file itself."""
    conn = _connect()
    conn.execute(
        "INSERT OR REPLACE INTO movies (id, title, watched_where, watched_at, rating) VALUES (?, ?, ?, ?, ?)",
        (row["id"], row["title"], row["watched_where"], row["watched_at"], row["rating"]),
    )
    conn.commit()
    conn.close()
