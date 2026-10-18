"""
ÆTHER — write-capable MCP server.

Eternal_Master_Build_Plan.md Phase 4: "ÆTHER wired in as a write-capable
MCP capability with per-tool consent + audit trail." Every tool below is a
genuine write (habits, habit_logs, workouts, expenses, activities, dreams,
calendar_events, memories) against the SQLite mirror in data.py, which is
a field-for-field port of ÆTHER's real Prisma schema (see data.py's
docstring for the honest-grounding note — this is stronger footing than
Phase 3's CinemaVault mirror had, since ÆTHER's actual schema.prisma was
available in full rather than reconstructed from a feature doc).

None of these tools decide on their own whether they're allowed to run —
that's voice_consent_gateway.registry_ext (sensitivity tier), voice_consent_gateway.consent
(per-tool consent), and voice_consent_gateway.voice_gate (voice-vs-non-voice channel),
composed together in voice_consent_gateway.gateway.Phase4Gateway. This server, like
Phase 3's real MCP servers, only knows how to do the thing once asked;
RCAP-P's rings (ROTATE/INSPECT/CONTAIN/ATTRIBUTE, imported unmodified from
Phase 3) still wrap every call the same way they wrap CinemaVault/
study_tracker calls — a write tool answering through a compromised or
buggy path is exactly the kind of tool response INSPECT was generalized
in Phase 3 to catch, regardless of whether the tool reads or writes.

Same flat-`Annotated[..., Field(...)]`-arguments convention Phase 3
settled on for the same reason: matches what a caller's on-the-wire
`arguments` dict naturally looks like, no nesting convention to remember.
"""
import sys
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data  # noqa: E402

mcp = FastMCP("aether_mcp")


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


def _wrap_value_error(fn, *args, **kwargs):
    """ÆTHER's data layer raises AetherStoreError (a ValueError subclass)
    on bad input — surfaced here as a plain ValueError so RCAP-P's gateway
    (which already turns a tool-side ValueError into a clean, non-crashing
    ToolCallResult) handles it the same way it handles CinemaVault's
    `no movie logged with id` case, no special-casing needed upstream."""
    try:
        return fn(*args, **kwargs)
    except data.AetherStoreError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="aether_create_habit",
    annotations={"title": "Create a new habit", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def aether_create_habit(
    title: Annotated[str, Field(min_length=1, max_length=200, description="Habit name, e.g. 'Morning Run'")],
    category: Annotated[str, Field(description="One of ÆTHER's habit categories, e.g. Exercise/Learning/Mindfulness/Reading/Nutrition/Other")] = "Other",
) -> HabitRecord:
    """Create a habit in ÆTHER's Habit module. Returns the created habit."""
    row = _wrap_value_error(data.get_store().create_habit, title=title, category=category)
    return HabitRecord(**{k: row[k] for k in ("id", "title", "category", "current_streak", "longest_streak")})


@mcp.tool(
    name="aether_log_habit",
    annotations={"title": "Log a habit as done", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def aether_log_habit(
    habit_title: Annotated[str, Field(min_length=1, max_length=200,
                                       description="Habit name. Created automatically if it doesn't exist yet — see data.py's get_or_create_habit_by_title docstring.")],
    date_key: Annotated[str, Field(description="'today' or an explicit YYYY-MM-DD date")] = "today",
) -> HabitLogResult:
    """Mark a habit done for a given day. Idempotent per day: logging the
    same habit twice on the same date_key is a no-op the second time
    (already_logged_today=True), matching ÆTHER's own toggle semantics."""
    store = data.get_store()
    habit = _wrap_value_error(store.get_or_create_habit_by_title, title=habit_title)
    result = _wrap_value_error(store.log_habit, habit_id=habit["id"], date_key=date_key)
    return HabitLogResult(
        habit=HabitRecord(**{k: result["habit"][k] for k in ("id", "title", "category", "current_streak", "longest_streak")}),
        already_logged_today=result["already_logged_today"],
        date_key=result["date_key"],
    )


@mcp.tool(
    name="aether_add_expense",
    annotations={"title": "Add an expense", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def aether_add_expense(
    name: Annotated[str, Field(min_length=1, max_length=200, description="What was purchased, e.g. 'Lunch'")],
    amount: Annotated[float, Field(gt=0, description="Positive amount spent")],
    category: Annotated[str, Field(description="clothing/footwear/food/bills/entertainment/other")] = "other",
) -> ExpenseRecord:
    """Add an expense to ÆTHER's Expense module (currency fixed at INR,
    matching ÆTHER's schema default)."""
    row = _wrap_value_error(data.get_store().add_expense, name=name, amount=amount, category=category)
    return ExpenseRecord(**{k: row[k] for k in ("id", "name", "amount", "currency", "category", "date")})


@mcp.tool(
    name="aether_log_workout",
    annotations={"title": "Log a workout", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def aether_log_workout(
    workout_type: Annotated[str, Field(min_length=1, description="Running/Cycling/Gym/Yoga/Swimming/Walking/Other")],
    duration_min: Annotated[int, Field(gt=0, le=1440, description="Duration in minutes")],
    notes: Annotated[str, Field(max_length=1000, description="Optional notes")] = "",
) -> WorkoutRecord:
    """Log a workout in ÆTHER's Workout module."""
    row = _wrap_value_error(data.get_store().log_workout, workout_type=workout_type, duration_min=duration_min, notes=notes)
    return WorkoutRecord(**{k: row[k] for k in ("id", "type", "duration_min", "notes", "performed_at")})


@mcp.tool(
    name="aether_log_activity",
    annotations={"title": "Log a movie/skill/game/YouTube/project activity", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def aether_log_activity(
    activity_type: Annotated[str, Field(description="One of MOVIE, SKILL, GAME, YOUTUBE, PROJECT")],
    title: Annotated[str, Field(min_length=1, max_length=300, description="Title, e.g. movie name or project name")],
    duration_min: Annotated[Optional[int], Field(default=None, ge=0, description="Optional duration in minutes")] = None,
) -> ActivityRecord:
    """Log an entry in ÆTHER's Activity module (movies/skills/games/YouTube/projects)."""
    row = _wrap_value_error(data.get_store().log_activity, activity_type=activity_type, title=title, duration_min=duration_min)
    return ActivityRecord(**{k: row[k] for k in ("id", "type", "title", "metadata", "duration_min", "performed_at")})


@mcp.tool(
    name="aether_add_dream",
    annotations={"title": "Log a dream journal entry", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def aether_add_dream(
    title: Annotated[str, Field(min_length=1, max_length=300, description="Short dream title")],
    content: Annotated[str, Field(max_length=4000, description="Dream description")] = "",
    date_key: Annotated[str, Field(description="'today' or an explicit YYYY-MM-DD date")] = "today",
) -> DreamRecord:
    """Add an entry to ÆTHER's Dream journal.

    Data-classification note (build plan Section 6.3, point 3): dream
    journal content is always LOCAL_ONLY for any spoken *readback* of this
    entry — see voice/data_classification.py's MemoryCategory.DREAM_JOURNAL
    handling — but this tool call itself, like every other write here,
    still goes through RCAP-P's INSPECT ring the same as any other tool
    response, per Section 6.3 point 4's "no exception" rule.
    """
    row = _wrap_value_error(data.get_store().add_dream, title=title, content=content, date_key=date_key)
    return DreamRecord(**{k: row[k] for k in ("id", "title", "content", "date_key")})


@mcp.tool(
    name="aether_add_calendar_event",
    annotations={"title": "Add a calendar event", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def aether_add_calendar_event(
    title: Annotated[str, Field(min_length=1, max_length=300, description="Event title, e.g. \"Mom's Birthday\"")],
    date: Annotated[str, Field(description="ISO date, e.g. '2026-06-15'")],
    event_type: Annotated[str, Field(description="One of BIRTHDAY, ANNIVERSARY, REMINDER, ACHIEVEMENT")] = "REMINDER",
    recurring: Annotated[bool, Field(description="Whether this repeats yearly")] = True,
) -> CalendarEventRecord:
    """Add an event to ÆTHER's Life Calendar module."""
    row = _wrap_value_error(
        data.get_store().add_calendar_event, title=title, event_type=event_type, date=date, recurring=recurring
    )
    return CalendarEventRecord(id=row["id"], title=row["title"], type=row["type"], date=row["date"], recurring=bool(row["recurring"]))


@mcp.tool(
    name="aether_add_memory",
    annotations={"title": "Save a memory/achievement", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def aether_add_memory(
    title: Annotated[str, Field(min_length=1, max_length=300, description="e.g. 'First Marathon Completed!'")],
    content: Annotated[str, Field(max_length=4000, description="The story behind this memory")] = "",
    emoji: Annotated[str, Field(max_length=8, description="A representative emoji")] = "✨",
) -> MemoryRecord:
    """Save an entry to ÆTHER's Memories/Achievements module."""
    row = _wrap_value_error(data.get_store().add_memory, title=title, content=content, emoji=emoji)
    return MemoryRecord(**{k: row[k] for k in ("id", "title", "content", "emoji", "date")})


if __name__ == "__main__":
    data.get_store()  # ensure schema exists before serving
    mcp.run()
