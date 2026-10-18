"""
Phase 4 registry extension.

Composes with rcap_p.registry rather than replacing it: `build_registry()`
below returns Phase 3's own read-only registry (unmodified, imported
directly) plus Phase 4's new WRITE-tier ÆTHER tools. Same
default-vs-demo split Phase 3 established for exactly the same reason —
`build_demo_registry()` additionally wires in the SENSITIVE-tier fixture
(mcp_servers/sensitive_demo), which must never be reachable from a real
deployment's registry.

Output schemas mirror mcp_servers/aether/server.py's Pydantic models —
kept here rather than imported from server.py, matching the precedent
rcap_p.registry already set for cinemavault/study_tracker (the registry
is the single place that answers "what should this tool's response look
like," independent of whether the calling code can even import the
server module).
"""
from typing import Any, Dict, Optional

from pydantic import BaseModel

from rcap_p.models import ToolManifestEntry, ToolSensitivity
from rcap_p.registry import build_default_registry as build_rcap_p_default_registry
from rcap_p.registry import build_demo_registry as build_rcap_p_demo_registry


class HabitRecord(BaseModel):
    id: str
    title: str
    category: str
    current_streak: int
    longest_streak: int


class HabitLogResult(BaseModel):
    habit: HabitRecord
    already_logged_today: bool
    date_key: str


class ExpenseRecord(BaseModel):
    id: str
    name: str
    amount: float
    currency: str
    category: str
    date: str


class WorkoutRecord(BaseModel):
    id: str
    type: str
    duration_min: int
    notes: str
    performed_at: str


class ActivityRecord(BaseModel):
    id: str
    type: str
    title: str
    metadata: Dict[str, Any]
    duration_min: Optional[int]
    performed_at: str


class DreamRecord(BaseModel):
    id: str
    title: str
    content: str
    date_key: str


class CalendarEventRecord(BaseModel):
    id: str
    title: str
    type: str
    date: str
    recurring: bool


class MemoryRecord(BaseModel):
    id: str
    title: str
    content: str
    emoji: str
    date: str


def _aether_entries() -> Dict[str, ToolManifestEntry]:
    specs = [
        ("aether_create_habit", "Create a new habit in ÆTHER's Habit module.", HabitRecord),
        ("aether_log_habit", "Log a habit as done for a given day (auto-creates the habit if new).", HabitLogResult),
        ("aether_add_expense", "Add an expense to ÆTHER's Expense module.", ExpenseRecord),
        ("aether_log_workout", "Log a workout in ÆTHER's Workout module.", WorkoutRecord),
        ("aether_log_activity", "Log a movie/skill/game/YouTube/project activity.", ActivityRecord),
        ("aether_add_dream", "Add a dream-journal entry.", DreamRecord),
        ("aether_add_calendar_event", "Add a birthday/anniversary/reminder/achievement event.", CalendarEventRecord),
        ("aether_add_memory", "Save a memory/achievement.", MemoryRecord),
    ]
    return {
        name: ToolManifestEntry(
            tool_name=name,
            server_key="aether",
            description=desc,
            sensitivity=ToolSensitivity.WRITE,
            output_schema=schema,
            rate_limit_per_minute=30,
        )
        for name, desc, schema in specs
    }


def build_registry() -> Dict[str, ToolManifestEntry]:
    """The real Phase 4 registry: Phase 3's real (non-demo) registry,
    unmodified, plus ÆTHER's WRITE-tier tools. No sensitive_demo entry —
    same reasoning as rcap_p.registry.build_default_registry()."""
    registry = dict(build_rcap_p_default_registry())
    registry.update(_aether_entries())
    return registry


def build_demo_registry() -> Dict[str, ToolManifestEntry]:
    """Adds Phase 3's demo fixtures (malicious_test) AND Phase 4's
    SENSITIVE-tier demo fixture (sensitive_demo) on top of the real
    registry. Demo/test use only — see mcp_servers/sensitive_demo/server.py's
    module docstring for why this fixture exists at all."""
    registry = dict(build_rcap_p_demo_registry())
    registry.update(_aether_entries())
    registry["sensitive_demo_unlock_door"] = ToolManifestEntry(
        tool_name="sensitive_demo_unlock_door",
        server_key="sensitive_demo",
        description="DEMO/FIXTURE ONLY — stands in for Phase 5/6's physical Home "
                     "Assistant lock control. Never registered outside demo/tests.",
        sensitivity=ToolSensitivity.SENSITIVE,
        rate_limit_per_minute=30,
    )
    return registry
