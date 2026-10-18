import pytest

from mcp_servers.aether.data import AetherStore, AetherStoreError


@pytest.fixture()
def store(tmp_path):
    return AetherStore(str(tmp_path / "aether_test.db"))


def test_create_and_get_habit(store):
    h = store.create_habit("Morning Run", category="Exercise")
    assert h["title"] == "Morning Run"
    assert h["current_streak"] == 0
    assert store.get_habit(h["id"])["id"] == h["id"]


def test_get_or_create_is_idempotent_on_title(store):
    h1 = store.get_or_create_habit_by_title("Morning Run")
    h2 = store.get_or_create_habit_by_title("morning run")  # case-insensitive match
    assert h1["id"] == h2["id"]


def test_log_habit_increments_streak(store):
    h = store.create_habit("Morning Run")
    r1 = store.log_habit(h["id"], date_key="2026-08-01")
    assert r1["habit"]["current_streak"] == 1
    r2 = store.log_habit(h["id"], date_key="2026-08-02")
    assert r2["habit"]["current_streak"] == 2


def test_log_habit_same_day_is_idempotent(store):
    h = store.create_habit("Morning Run")
    store.log_habit(h["id"], date_key="2026-08-01")
    r2 = store.log_habit(h["id"], date_key="2026-08-01")
    assert r2["already_logged_today"] is True
    assert r2["habit"]["current_streak"] == 1  # unchanged


def test_log_habit_unknown_id_raises(store):
    with pytest.raises(AetherStoreError):
        store.log_habit("nonexistent-id")


def test_add_expense_positive_amount(store):
    e = store.add_expense("Lunch", 200, category="food")
    assert e["amount"] == 200.0
    assert e["currency"] == "INR"


@pytest.mark.parametrize("bad_amount", [0, -5, None])
def test_add_expense_rejects_non_positive_amount(store, bad_amount):
    with pytest.raises(AetherStoreError):
        store.add_expense("bad", bad_amount)


def test_log_workout(store):
    w = store.log_workout("Cycling", 45, notes="felt good")
    assert w["duration_min"] == 45


def test_log_workout_rejects_non_positive_duration(store):
    with pytest.raises(AetherStoreError):
        store.log_workout("Cycling", 0)


def test_log_activity_validates_type(store):
    a = store.log_activity("MOVIE", "Interstellar", metadata={"where": "Theatre", "rating": 9})
    assert a["metadata"]["rating"] == 9
    with pytest.raises(AetherStoreError):
        store.log_activity("NOT_A_TYPE", "x")


def test_add_dream_resolves_today(store):
    d = store.add_dream("Flying", content="vivid", date_key="today")
    import datetime
    assert d["date_key"] == datetime.date.today().isoformat()


def test_add_calendar_event_validates_type_and_requires_date(store):
    ev = store.add_calendar_event("Mom's Birthday", event_type="BIRTHDAY", date="2026-06-15")
    assert ev["type"] == "BIRTHDAY"
    with pytest.raises(AetherStoreError):
        store.add_calendar_event("Bad", event_type="NOT_A_TYPE", date="2026-06-15")
    with pytest.raises(AetherStoreError):
        store.add_calendar_event("No date", date=None)


def test_add_memory(store):
    m = store.add_memory("First marathon", content="4:32", emoji="🏆")
    assert m["emoji"] == "🏆"


def test_list_helpers_reflect_writes(store):
    store.create_habit("A")
    store.create_habit("B")
    store.add_expense("x", 10)
    store.log_workout("Yoga", 20)
    assert len(store.list_habits()) == 2
    assert len(store.list_expenses()) == 1
    assert len(store.list_workouts()) == 1
