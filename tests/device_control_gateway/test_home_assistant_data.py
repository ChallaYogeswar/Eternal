"""
Unit tests for mcp_servers.home_assistant.data — the fixture client's
real state transitions, and RealHomeAssistantClient's seam error when
unconfigured (checked directly, not assumed from the docstring).
"""
import pytest

from mcp_servers.home_assistant.data import (
    FixtureHomeAssistantClient, HomeAssistantUnavailableError, RealHomeAssistantClient,
)


def test_default_entities_seeded(ha_client):
    state = ha_client.get_state("lock.front_door")
    assert state.state == "locked"
    state2 = ha_client.get_state("lock.back_door")
    assert state2.state == "unlocked"


def test_lock_then_unlock_round_trip(ha_client):
    ha_client.call_service("lock", "unlock", "lock.front_door")
    assert ha_client.get_state("lock.front_door").state == "unlocked"
    ha_client.call_service("lock", "lock", "lock.front_door")
    assert ha_client.get_state("lock.front_door").state == "locked"


def test_lock_is_idempotent(ha_client):
    """Locking an already-locked entity should succeed and reflect the
    same state, exactly like a real lock would, not error out."""
    result1 = ha_client.call_service("lock", "lock", "lock.front_door")
    result2 = ha_client.call_service("lock", "lock", "lock.front_door")
    assert result1.state == result2.state == "locked"


def test_turn_on_off_light(ha_client):
    ha_client.call_service("light", "turn_on", "light.living_room")
    assert ha_client.get_state("light.living_room").state == "on"
    ha_client.call_service("light", "turn_off", "light.living_room")
    assert ha_client.get_state("light.living_room").state == "off"


def test_unknown_entity_raises(ha_client):
    with pytest.raises(HomeAssistantUnavailableError):
        ha_client.get_state("lock.does_not_exist")
    with pytest.raises(HomeAssistantUnavailableError):
        ha_client.call_service("lock", "lock", "lock.does_not_exist")


def test_domain_mismatch_raises(ha_client):
    """Calling a lock service against a light entity should be refused,
    exactly like the real HA REST API would reject a domain mismatch."""
    with pytest.raises(HomeAssistantUnavailableError):
        ha_client.call_service("lock", "lock", "light.living_room")


def test_unknown_service_raises(ha_client):
    with pytest.raises(HomeAssistantUnavailableError):
        ha_client.call_service("lock", "explode", "lock.front_door")


def test_sensor_reads_correctly(ha_client):
    state = ha_client.get_state("sensor.front_door_battery")
    assert state.state == "87"
    assert state.attributes.get("unit") == "%"


def test_fixture_instances_are_independent(ha_client):
    """Two separately-constructed fixtures must not share mutable state —
    a real bug class (module-level mutable defaults) this test would
    catch if it existed."""
    other = FixtureHomeAssistantClient()
    ha_client.call_service("lock", "unlock", "lock.front_door")
    assert other.get_state("lock.front_door").state == "locked"


def test_real_client_raises_when_unconfigured(monkeypatch):
    import mcp_servers.home_assistant.data as data_mod
    monkeypatch.setattr(data_mod, "HOME_ASSISTANT_BASE_URL", "")
    client = RealHomeAssistantClient()
    with pytest.raises(HomeAssistantUnavailableError):
        client.get_state("lock.front_door")
    with pytest.raises(HomeAssistantUnavailableError):
        client.call_service("lock", "lock", "lock.front_door")


def test_get_client_singleton_defaults_to_fixture(monkeypatch, tmp_path):
    import mcp_servers.home_assistant.data as data_mod
    monkeypatch.delenv("PHASE5_HA_REAL", raising=False)
    # isolate the state path too — get_client() persists to disk now, and
    # without this the test would write into the package's own default
    # location on disk instead of a throwaway tmp_path.
    monkeypatch.setenv("PHASE5_HA_STATE_PATH", str(tmp_path / "ha_state.json"))
    data_mod.reset_client_for_tests()
    client = data_mod.get_client()
    assert isinstance(client, FixtureHomeAssistantClient)
    assert data_mod.get_client() is client
