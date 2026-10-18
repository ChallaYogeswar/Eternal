"""
ÆTHER data layer for Phase 4 — the write-capable counterpart to Phase 3's
read-only CinemaVault/study-tracker mirrors (mcp_servers/cinemavault/data.py,
mcp_servers/study_tracker/data.py), same seam pattern, stronger grounding:
ÆTHER's REAL schema is available in full (schema.prisma) rather than
reconstructed from a feature doc, so this mirror's tables are a direct,
field-for-field port of the real Prisma models — not an approximation.

Real, tested SQLite mirror now; pointing this at ÆTHER's actual
Postgres+Express backend instead (the README.md API reference: `POST
/api/habits`, `POST /api/habits/:id/log`, `POST /api/expenses`, etc.) is a
change local to this file — server.py's tool functions call the module-
level functions below and don't know or care which storage backs them,
exactly the CinemaVault precedent.

Field names are snake_case Python-side (matching the CinemaVault
precedent) with the exact Prisma field each maps to noted per table.
"""
import datetime as dt
import json
import os
import sqlite3
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS habits (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Other',
    emoji TEXT NOT NULL DEFAULT '⭐',
    color TEXT NOT NULL DEFAULT '#00e5ff',
    recurrence TEXT NOT NULL DEFAULT 'DAILY',
    is_active INTEGER NOT NULL DEFAULT 1,
    current_streak INTEGER NOT NULL DEFAULT 0,
    longest_streak INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS habit_logs (
    id TEXT PRIMARY KEY,
    habit_id TEXT NOT NULL,
    performed_at TEXT NOT NULL,
    date_key TEXT NOT NULL,
    notes TEXT DEFAULT '',
    UNIQUE(habit_id, date_key)
);

CREATE TABLE IF NOT EXISTS workouts (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    duration_min INTEGER NOT NULL,
    notes TEXT DEFAULT '',
    performed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    category TEXT NOT NULL,
    merchant TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    date TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    duration_min INTEGER,
    performed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'REMINDER',
    date TEXT NOT NULL,
    recurring INTEGER NOT NULL DEFAULT 1,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dreams (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT DEFAULT '',
    date_key TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT DEFAULT '',
    emoji TEXT NOT NULL DEFAULT '✨',
    date TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

VALID_ACTIVITY_TYPES = {"MOVIE", "SKILL", "GAME", "YOUTUBE", "PROJECT"}  # Prisma ActivityType enum
VALID_EVENT_TYPES = {"BIRTHDAY", "ANNIVERSARY", "REMINDER", "ACHIEVEMENT"}  # Prisma EventType enum


class AetherStoreError(ValueError):
    pass


class HttpAetherStore:
    """Authenticated adapter for the real ÆTHER Express/Postgres API."""

    def __init__(self, base_url: str, bearer_token: str, timeout: float = 10.0):
        if not base_url or not bearer_token:
            raise AetherStoreError("AETHER_API_URL and AETHER_API_TOKEN are required for http backend")
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, method=method,
            headers={"Authorization": f"Bearer {self.bearer_token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise AetherStoreError(f"ÆTHER API request failed: {exc}") from exc

    @staticmethod
    def _habit(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": row["id"], "title": row["title"], "category": row.get("category", "Other"),
            "current_streak": row.get("currentStreak", row.get("current_streak", 0)),
            "longest_streak": row.get("longestStreak", row.get("longest_streak", 0)),
        }

    def create_habit(self, title: str, category: str = "Other", **_: Any) -> Dict[str, Any]:
        return self._habit(self._request("POST", "/api/habits", {"title": title, "category": category}))

    def get_or_create_habit_by_title(self, title: str, category: str = "Other") -> Dict[str, Any]:
        habits = self._request("GET", "/api/habits")
        for habit in habits:
            if str(habit.get("title", "")).casefold() == title.casefold() and habit.get("isActive", True):
                return self._habit(habit)
        return self.create_habit(title, category)

    def log_habit(self, habit_id: str, date_key: Optional[str] = None, **_: Any) -> Dict[str, Any]:
        resolved_date = _resolve_date_key(date_key)
        before = self._request("GET", "/api/habits")
        habit_row = next((row for row in before if row.get("id") == habit_id), None)
        if habit_row is None:
            raise AetherStoreError(f"no habit with id {habit_id!r}")
        if habit_row.get("doneToday") and resolved_date == dt.date.today().isoformat():
            return {"habit": self._habit(habit_row), "already_logged_today": True, "date_key": resolved_date}
        outcome = self._request("POST", f"/api/habits/{habit_id}/log", {"dateKey": resolved_date})
        if not outcome.get("done", False):
            raise AetherStoreError("ÆTHER API toggled an existing habit log off; refusing a non-idempotent result")
        after = self._request("GET", "/api/habits")
        updated = next((row for row in after if row.get("id") == habit_id), habit_row)
        return {"habit": self._habit(updated), "already_logged_today": False, "date_key": resolved_date}

    def add_expense(self, name: str, amount: float, category: str = "other", **_: Any) -> Dict[str, Any]:
        row = self._request("POST", "/api/expenses", {"name": name, "amount": amount, "category": category})
        return {"id": row["id"], "name": row["name"], "amount": row["amount"],
                "currency": row.get("currency", "INR"), "category": row["category"],
                "date": row.get("date", row.get("createdAt", ""))}

    def log_workout(self, workout_type: str, duration_min: int, notes: str = "", **_: Any) -> Dict[str, Any]:
        row = self._request("POST", "/api/workouts", {"type": workout_type, "durationMin": duration_min, "notes": notes})
        return {"id": row["id"], "type": row.get("type", workout_type), "duration_min": row.get("durationMin", duration_min),
                "notes": row.get("notes", notes), "performed_at": row.get("performedAt", row.get("createdAt", ""))}

    def log_activity(self, activity_type: str, title: str, duration_min: Optional[int] = None, **_: Any) -> Dict[str, Any]:
        payload = {"type": activity_type, "title": title}
        if duration_min is not None:
            payload["durationMin"] = duration_min
        row = self._request("POST", "/api/activities", payload)
        return {"id": row["id"], "type": row["type"], "title": row["title"], "metadata": row.get("metadata", {}),
                "duration_min": row.get("durationMin"), "performed_at": row.get("performedAt", row.get("createdAt", ""))}

    def add_dream(self, title: str, content: str = "", date_key: str = "today", **_: Any) -> Dict[str, Any]:
        row = self._request("POST", "/api/calendar/dreams", {"title": title, "content": content, "dateKey": _resolve_date_key(date_key)})
        return {"id": row["id"], "title": row["title"], "content": row.get("content", ""), "date_key": row.get("dateKey", date_key)}

    def add_calendar_event(self, title: str, date: str, event_type: str = "REMINDER", recurring: bool = True, **_: Any) -> Dict[str, Any]:
        row = self._request("POST", "/api/calendar/events", {"title": title, "date": date, "type": event_type, "recurring": recurring})
        return {"id": row["id"], "title": row["title"], "type": row["type"], "date": row["date"], "recurring": row["recurring"]}

    def add_memory(self, title: str, content: str = "", emoji: str = "✨", **_: Any) -> Dict[str, Any]:
        row = self._request("POST", "/api/memories", {"title": title, "content": content, "emoji": emoji})
        return {"id": row["id"], "title": row["title"], "content": row.get("content", ""), "emoji": row.get("emoji", emoji),
                "date": row.get("date", row.get("createdAt", ""))}


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _resolve_date_key(date_key: Optional[str]) -> str:
    if not date_key or date_key.lower() == "today":
        return dt.date.today().isoformat()
    return date_key


def _new_id() -> str:
    return str(uuid.uuid4())


class AetherStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._connect()
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.close()

    # ---------------------------------------------------------------- habits
    def create_habit(self, title: str, category: str = "Other", emoji: str = "⭐",
                      color: str = "#00e5ff", recurrence: str = "DAILY") -> Dict[str, Any]:
        if not title or not title.strip():
            raise AetherStoreError("habit title must not be empty")
        habit_id = _new_id()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO habits (id, title, category, emoji, color, recurrence, is_active, "
                "current_streak, longest_streak, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, 0, 0, ?)",
                (habit_id, title.strip(), category, emoji, color, recurrence, _now_iso()),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_habit(habit_id)

    def get_habit(self, habit_id: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM habits WHERE id = ?", (habit_id,)).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def find_habit_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM habits WHERE lower(title) = lower(?) AND is_active = 1", (title,)
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def get_or_create_habit_by_title(self, title: str, category: str = "Other") -> Dict[str, Any]:
        """Phase 4 UX decision, stated explicitly rather than hidden: a
        voice command like "log my run habit" should work even the first
        time, without a separate "create habit" step — ÆTHER's own web UI
        requires creating a habit before logging it, but that friction
        exists for a mouse-and-modal interface, not a spoken one. Logged
        to the ledger with an explanation either way (server.py), so this
        convenience is auditable, not invisible."""
        existing = self.find_habit_by_title(title)
        if existing:
            return existing
        return self.create_habit(title=title, category=category)

    def list_habits(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM habits WHERE is_active = 1 ORDER BY created_at").fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def log_habit(self, habit_id: str, date_key: Optional[str] = None, notes: str = "") -> Dict[str, Any]:
        habit = self.get_habit(habit_id)
        if habit is None:
            raise AetherStoreError(f"no habit with id {habit_id!r}")
        resolved_date_key = _resolve_date_key(date_key)
        conn = self._connect()
        try:
            already_logged = conn.execute(
                "SELECT 1 FROM habit_logs WHERE habit_id = ? AND date_key = ?",
                (habit_id, resolved_date_key),
            ).fetchone()
            if already_logged:
                return {"habit": habit, "already_logged_today": True, "date_key": resolved_date_key}

            conn.execute(
                "INSERT INTO habit_logs (id, habit_id, performed_at, date_key, notes) VALUES (?, ?, ?, ?, ?)",
                (_new_id(), habit_id, _now_iso(), resolved_date_key, notes),
            )
            new_streak = habit["current_streak"] + 1
            new_longest = max(new_streak, habit["longest_streak"])
            conn.execute(
                "UPDATE habits SET current_streak = ?, longest_streak = ? WHERE id = ?",
                (new_streak, new_longest, habit_id),
            )
            conn.commit()
        finally:
            conn.close()
        updated = self.get_habit(habit_id)
        return {"habit": updated, "already_logged_today": False, "date_key": resolved_date_key}

    # -------------------------------------------------------------- workouts
    def log_workout(self, workout_type: str, duration_min: int, notes: str = "",
                     performed_at: Optional[str] = None) -> Dict[str, Any]:
        if duration_min is None or duration_min <= 0:
            raise AetherStoreError("duration_min must be a positive number of minutes")
        workout_id = _new_id()
        performed = performed_at or _now_iso()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO workouts (id, type, duration_min, notes, performed_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (workout_id, workout_type, int(duration_min), notes, performed, _now_iso()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM workouts WHERE id = ?", (workout_id,)).fetchone()
        finally:
            conn.close()
        return dict(row)

    def list_workouts(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM workouts ORDER BY performed_at DESC").fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- expenses
    def add_expense(self, name: str, amount: float, category: str = "other",
                     currency: str = "INR", merchant: str = "", notes: str = "",
                     date: Optional[str] = None) -> Dict[str, Any]:
        if not name or not name.strip():
            raise AetherStoreError("expense name must not be empty")
        if amount is None or amount <= 0:
            raise AetherStoreError("expense amount must be a positive number")
        expense_id = _new_id()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO expenses (id, name, amount, currency, category, merchant, notes, date, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (expense_id, name.strip(), float(amount), currency, category, merchant, notes,
                 date or _now_iso(), _now_iso()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        finally:
            conn.close()
        return dict(row)

    def list_expenses(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM expenses ORDER BY date DESC").fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- activities
    def log_activity(self, activity_type: str, title: str, metadata: Optional[Dict[str, Any]] = None,
                      duration_min: Optional[int] = None, performed_at: Optional[str] = None) -> Dict[str, Any]:
        activity_type = activity_type.upper()
        if activity_type not in VALID_ACTIVITY_TYPES:
            raise AetherStoreError(f"activity_type must be one of {sorted(VALID_ACTIVITY_TYPES)}, got {activity_type!r}")
        if not title or not title.strip():
            raise AetherStoreError("activity title must not be empty")
        activity_id = _new_id()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO activities (id, type, title, metadata, duration_min, performed_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (activity_id, activity_type, title.strip(), json.dumps(metadata or {}),
                 duration_min, performed_at or _now_iso(), _now_iso()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM activities WHERE id = ?", (activity_id,)).fetchone()
        finally:
            conn.close()
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        return result

    def list_activities(self, activity_type: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            if activity_type:
                rows = conn.execute(
                    "SELECT * FROM activities WHERE type = ? ORDER BY performed_at DESC", (activity_type.upper(),)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM activities ORDER BY performed_at DESC").fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            d = dict(r)
            d["metadata"] = json.loads(d["metadata"])
            out.append(d)
        return out

    # ----------------------------------------------------------------- dreams
    def add_dream(self, title: str, content: str = "", date_key: Optional[str] = None) -> Dict[str, Any]:
        if not title or not title.strip():
            raise AetherStoreError("dream title must not be empty")
        dream_id = _new_id()
        resolved_date_key = _resolve_date_key(date_key)
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO dreams (id, title, content, date_key, created_at) VALUES (?, ?, ?, ?, ?)",
                (dream_id, title.strip(), content, resolved_date_key, _now_iso()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM dreams WHERE id = ?", (dream_id,)).fetchone()
        finally:
            conn.close()
        return dict(row)

    # ---------------------------------------------------------- calendar events
    def add_calendar_event(self, title: str, event_type: str = "REMINDER", date: Optional[str] = None,
                            recurring: bool = True, notes: str = "") -> Dict[str, Any]:
        event_type = event_type.upper()
        if event_type not in VALID_EVENT_TYPES:
            raise AetherStoreError(f"event_type must be one of {sorted(VALID_EVENT_TYPES)}, got {event_type!r}")
        if not title or not title.strip():
            raise AetherStoreError("event title must not be empty")
        if not date:
            raise AetherStoreError("event date must be provided (ISO date string)")
        event_id = _new_id()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO calendar_events (id, title, type, date, recurring, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, title.strip(), event_type, date, 1 if recurring else 0, notes, _now_iso()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
        finally:
            conn.close()
        return dict(row)

    # --------------------------------------------------------------- memories
    def add_memory(self, title: str, content: str = "", emoji: str = "✨",
                    date: Optional[str] = None) -> Dict[str, Any]:
        if not title or not title.strip():
            raise AetherStoreError("memory title must not be empty")
        memory_id = _new_id()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO memories (id, title, content, emoji, date, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (memory_id, title.strip(), content, emoji, date or _now_iso(), _now_iso()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        finally:
            conn.close()
        return dict(row)


# ---------------------------------------------------------------------------
# Module-level DB_PATH + singleton, for consistency with the env-var-driven
# convention Phase 3's cinemavault/study_tracker data.py modules established
# (`CINEMAVAULT_DB_PATH`, read at import time so deployment.py can point the
# subprocess at the right file). AetherStore is a class rather than flat
# module functions — a deliberate, documented deviation from that precedent,
# not an oversight: ÆTHER's write surface spans 7 tables and 8+ operations,
# versus CinemaVault's single read-only table, and grouping that behind one
# instance-per-database-file avoids passing a path into every call. This
# module-level singleton is how server.py (which calls module functions, to
# stay import-consistent with the other MCP servers) gets one.
DB_PATH = os.environ.get("AETHER_DB_PATH", os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "aether_mirror.db"
))

_singleton: Optional[Any] = None


def get_store() -> Any:
    global _singleton
    if _singleton is None:
        if os.environ.get("AETHER_BACKEND", "sqlite").casefold() == "http":
            _singleton = HttpAetherStore(
                os.environ.get("AETHER_API_URL", ""),
                os.environ.get("AETHER_API_TOKEN", ""),
                float(os.environ.get("AETHER_API_TIMEOUT_SECONDS", "10")),
            )
        else:
            _singleton = AetherStore(DB_PATH)
    return _singleton
