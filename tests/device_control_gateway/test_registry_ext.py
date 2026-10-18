"""
Tests for device_control_gateway.registry_ext — composition correctness (Phase 4's
registry carried forward unmodified) and the specific tiering decisions
documented in the module's own docstring.
"""
from voice_consent_gateway.registry_ext import build_registry as build_phase4_registry
from device_control_gateway.registry_ext import build_demo_registry, build_registry
from rcap_p.models import ToolSensitivity

EXPECTED_TIERS = {
    "laptop_lock": ToolSensitivity.WRITE,
    "laptop_unlock": ToolSensitivity.SENSITIVE,
    "laptop_sleep": ToolSensitivity.WRITE,
    "laptop_open_app": ToolSensitivity.WRITE,
    "laptop_run_script": ToolSensitivity.WRITE,
    "laptop_shutdown": ToolSensitivity.SENSITIVE,
    "remote_control_wake": ToolSensitivity.WRITE,
    "remote_control_request_full_session": ToolSensitivity.SENSITIVE,
    "ha_lock": ToolSensitivity.SENSITIVE,
    "ha_unlock": ToolSensitivity.SENSITIVE,
    "ha_turn_on": ToolSensitivity.WRITE,
    "ha_turn_off": ToolSensitivity.WRITE,
    "ha_get_state": ToolSensitivity.READ_ONLY,
}


def test_phase4_registry_carried_forward_unmodified():
    phase4_reg = build_phase4_registry()
    phase5_reg = build_registry()
    for name, entry in phase4_reg.items():
        assert name in phase5_reg
        assert phase5_reg[name].sensitivity == entry.sensitivity
        assert phase5_reg[name].server_key == entry.server_key


def test_all_thirteen_new_tools_present_with_documented_tiers():
    registry = build_registry()
    for name, expected_tier in EXPECTED_TIERS.items():
        assert name in registry, f"{name} missing from registry"
        assert registry[name].sensitivity == expected_tier, f"{name} expected {expected_tier}, got {registry[name].sensitivity}"


def test_registry_size_is_phase4_plus_thirteen():
    phase4_count = len(build_phase4_registry())
    phase5_count = len(build_registry())
    assert phase5_count == phase4_count + len(EXPECTED_TIERS)


def test_no_new_demo_only_fixture_introduced():
    """Phase 5 should add no NEW demo-only tool of its own — sensitive_demo
    (Phase 4) already served its purpose as the stand-in this phase
    replaces with the real thing."""
    demo_reg = build_demo_registry()
    prod_reg = build_registry()
    demo_only_names = set(demo_reg) - set(prod_reg)
    # every demo-only name should already have existed before Phase 5
    # (i.e. come from rcap_p/voice_consent_gateway's own fixtures), not be new here
    phase4_demo_only = set(__import__("voice_consent_gateway.registry_ext", fromlist=["build_demo_registry"]).build_demo_registry()) - set(build_phase4_registry())
    assert demo_only_names == phase4_demo_only


def test_server_keys_point_at_real_directories():
    import os
    base = os.path.join(os.path.dirname(__file__), "..", "..", "mcp_servers")
    registry = build_registry()
    for name, entry in registry.items():
        if entry.server_key in ("laptop_agent", "remote_control", "home_assistant"):
            server_dir = os.path.join(base, entry.server_key)
            assert os.path.isdir(server_dir), f"{entry.server_key} directory missing for tool {name}"
            assert os.path.isfile(os.path.join(server_dir, "server.py"))


def test_full_control_tool_has_lowest_rate_limit():
    """Sanity check on the PERIMETER rate limit chosen for the single
    most sensitive action — should be the most conservative of the new
    tools, not an oversight copy-pasted from a routine one."""
    registry = build_registry()
    full_control_limit = registry["remote_control_request_full_session"].rate_limit_per_minute
    for name in EXPECTED_TIERS:
        if name != "remote_control_request_full_session":
            assert registry[name].rate_limit_per_minute >= full_control_limit
