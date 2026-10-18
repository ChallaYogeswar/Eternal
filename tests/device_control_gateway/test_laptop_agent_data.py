"""
Unit tests for mcp_servers.laptop_agent.data — the state machine itself,
independent of the MCP/gateway layers around it (those get their own
integration tests). Every state-transition rule the module docstring
claims is checked here directly, not assumed from the class existing.
"""
import pytest

from mcp_servers.laptop_agent.data import (
    ALLOWED_APPS, ALLOWED_SCRIPTS, LaptopActionError, LockState, PowerState,
    SimulatedLaptopActionProvider, WindowsLaptopActionProvider,
    MacOSLaptopActionProvider, LinuxLaptopActionProvider,
    PlatformActionUnavailableError, real_provider_for_this_platform,
)


def test_starts_awake_and_unlocked(laptop_provider):
    assert laptop_provider.power_state == PowerState.AWAKE
    assert laptop_provider.lock_state == LockState.UNLOCKED


def test_lock_then_unlock_round_trip(laptop_provider):
    r1 = laptop_provider.lock()
    assert r1.ok and r1.lock_state == LockState.LOCKED
    r2 = laptop_provider.unlock()
    assert r2.ok and r2.lock_state == LockState.UNLOCKED


def test_sleep_then_lock_fails(laptop_provider):
    """You cannot lock a sleeping machine — the state machine must say so,
    not silently pretend it worked."""
    laptop_provider.sleep()
    result = laptop_provider.lock()
    assert result.ok is False
    assert "asleep" in result.detail


def test_sleep_then_unlock_fails(laptop_provider):
    laptop_provider.sleep()
    result = laptop_provider.unlock()
    assert result.ok is False


def test_sleep_twice_reports_already_asleep(laptop_provider):
    laptop_provider.sleep()
    result = laptop_provider.sleep()
    assert result.ok is False
    assert "already" in result.detail


def test_shutdown_then_any_action_fails(laptop_provider):
    laptop_provider.shutdown()
    for action in (laptop_provider.lock, laptop_provider.unlock, laptop_provider.sleep):
        result = action()
        assert result.ok is False
    assert laptop_provider.open_app("browser").ok is False
    assert laptop_provider.run_script("backup_notes").ok is False


def test_shutdown_twice_reports_already_shutdown(laptop_provider):
    laptop_provider.shutdown()
    result = laptop_provider.shutdown()
    assert result.ok is False
    assert "already" in result.detail


@pytest.mark.parametrize("app_name", ALLOWED_APPS)
def test_open_each_allowed_app_succeeds(laptop_provider, app_name):
    result = laptop_provider.open_app(app_name)
    assert result.ok is True
    assert app_name in result.detail


def test_open_app_not_on_allowlist_raises():
    """The whole point of the fixed-action-list design: an arbitrary app
    name is refused, not attempted."""
    provider = SimulatedLaptopActionProvider()
    with pytest.raises(LaptopActionError):
        provider.open_app("literally anything the caller wants")


def test_open_app_while_locked_fails(laptop_provider):
    laptop_provider.lock()
    result = laptop_provider.open_app("browser")
    assert result.ok is False
    assert "locked" in result.detail


@pytest.mark.parametrize("script_name", ALLOWED_SCRIPTS)
def test_run_each_allowed_script_succeeds(laptop_provider, script_name):
    result = laptop_provider.run_script(script_name)
    assert result.ok is True


def test_run_script_not_on_allowlist_raises():
    provider = SimulatedLaptopActionProvider()
    with pytest.raises(LaptopActionError):
        provider.run_script("rm -rf /")


def test_run_script_error_message_lists_the_allowlist():
    """The error itself should be actionable, not just 'no'."""
    provider = SimulatedLaptopActionProvider()
    with pytest.raises(LaptopActionError) as exc_info:
        provider.run_script("not_a_real_script")
    for allowed in ALLOWED_SCRIPTS:
        assert allowed in str(exc_info.value)


@pytest.mark.parametrize("os_name,expected_cls", [
    ("Windows", WindowsLaptopActionProvider),
    ("Darwin", MacOSLaptopActionProvider),
    ("Linux", LinuxLaptopActionProvider),
])
def test_real_provider_dispatches_by_platform(monkeypatch, os_name, expected_cls):
    import mcp_servers.laptop_agent.data as data_mod
    monkeypatch.setattr(data_mod.platform, "system", lambda: os_name)
    provider = real_provider_for_this_platform()
    assert isinstance(provider, expected_cls)


def test_unknown_platform_raises():
    import mcp_servers.laptop_agent.data as data_mod

    original = data_mod.platform.system
    data_mod.platform.system = lambda: "PlanNine"
    try:
        with pytest.raises(PlatformActionUnavailableError):
            real_provider_for_this_platform()
    finally:
        data_mod.platform.system = original


@pytest.mark.parametrize("provider_cls", [WindowsLaptopActionProvider, MacOSLaptopActionProvider, LinuxLaptopActionProvider])
def test_every_real_provider_action_raises_the_documented_seam_error(provider_cls):
    """Every one of the six actions on every real per-OS provider must
    raise PlatformActionUnavailableError, not silently no-op and not
    actually touch this sandbox's own session — checked for all six
    methods on all three classes, not sampled."""
    provider = provider_cls()
    with pytest.raises(PlatformActionUnavailableError):
        provider.lock()
    with pytest.raises(PlatformActionUnavailableError):
        provider.unlock()
    with pytest.raises(PlatformActionUnavailableError):
        provider.sleep()
    with pytest.raises(PlatformActionUnavailableError):
        provider.open_app("browser")
    with pytest.raises(PlatformActionUnavailableError):
        provider.run_script("backup_notes")
    with pytest.raises(PlatformActionUnavailableError):
        provider.shutdown()


def test_get_provider_singleton_defaults_to_simulated(monkeypatch, tmp_path):
    import mcp_servers.laptop_agent.data as data_mod
    monkeypatch.delenv("PHASE5_LAPTOP_REAL", raising=False)
    # isolate the state path too — get_provider() persists to disk now,
    # and without this the test would write into the package's own
    # default location on disk instead of a throwaway tmp_path.
    monkeypatch.setenv("PHASE5_LAPTOP_STATE_PATH", str(tmp_path / "laptop_state.json"))
    data_mod.reset_provider_for_tests()
    provider = data_mod.get_provider()
    assert isinstance(provider, SimulatedLaptopActionProvider)
    # singleton: calling again returns the SAME object, not a fresh one
    assert data_mod.get_provider() is provider
