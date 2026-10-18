"""
Phase 5 registry extension.

Composes with voice_consent_gateway.registry_ext rather than replacing it: build_registry()
below returns Phase 4's own registry (unmodified, imported directly) plus
Phase 5's new laptop_agent / remote_control / home_assistant tools. Same
default-vs-demo split every earlier phase established for the same
reason; Phase 5 introduces no NEW demo-only fixture of its own —
mcp_servers/sensitive_demo (Phase 4) explicitly documented itself as
"stands in for Phase 5/6's physical Home Assistant lock control" (checked
directly in voice_consent_gateway/registry_ext.py before writing this file), and that
stand-in's job ends now that ha_lock/ha_unlock are real. build_demo_registry()
here still carries Phase 3/4's existing demo fixtures forward (malicious_test_*,
sensitive_demo) purely so Phase 5's own tests can still exercise those
older scenarios without losing coverage — nothing here is a substitute
for those older fixtures; it is strictly additive.

Tiering rationale (build plan Section 6.3 point 2 + Section 7.1),
summarized here for one-place reference; each server.py's own docstrings
carry the same reasoning locally:

  laptop_lock              WRITE      safe direction of lock/unlock
  laptop_unlock             SENSITIVE  risky direction — exposes an active session
  laptop_sleep              WRITE      safe direction, reduces exposure
  laptop_open_app           WRITE      fixed allow-list only, never arbitrary
  laptop_run_script         WRITE      fixed allow-list only, never arbitrary
  laptop_shutdown           SENSITIVE  high-consequence, not instantly reversible
  remote_control_wake       WRITE      waking alone does not expose control
  remote_control_request_full_session
                            SENSITIVE  "the single most sensitive action in
                                       the whole system" (build plan 7.1) —
                                       gets an ADDITIONAL top-trust-tier
                                       check in device_control_gateway/gateway.py, on top
                                       of the ordinary SENSITIVE voice-gate
  ha_lock / ha_unlock       SENSITIVE  build plan names "physical Home
                                       Assistant actions like locks"
                                       explicitly, both directions
  ha_turn_on / ha_turn_off  WRITE      lights/switches, no physical-
                                       security implication
  ha_get_state              READ_ONLY  state lookup only
"""
from typing import Any, Dict

from pydantic import BaseModel

from voice_consent_gateway.registry_ext import build_demo_registry as build_phase4_demo_registry
from voice_consent_gateway.registry_ext import build_registry as build_phase4_registry
from rcap_p.models import ToolManifestEntry, ToolSensitivity


class LaptopActionRecord(BaseModel):
    ok: bool
    action: str
    detail: str
    power_state: str | None = None
    lock_state: str | None = None


class WakeRecord(BaseModel):
    ok: bool
    target_mac: str
    bytes_sent: int
    sent_to: str
    detail: str


class FullControlHandoffRecord(BaseModel):
    wake_on_lan_sent: bool
    target_mac_address: str
    protocol: str
    connection_hint: str
    session_id: str
    expires_at: float
    note: str


class HaEntityStateRecord(BaseModel):
    entity_id: str
    domain: str
    state: str
    attributes: Dict[str, Any]


def _phase5_entries() -> Dict[str, ToolManifestEntry]:
    laptop_specs = [
        ("laptop_lock", "Lock the laptop's screen.", ToolSensitivity.WRITE),
        ("laptop_unlock", "Unlock the laptop's screen.", ToolSensitivity.SENSITIVE),
        ("laptop_sleep", "Suspend the laptop.", ToolSensitivity.WRITE),
        ("laptop_open_app", "Open one pre-registered, allow-listed application.", ToolSensitivity.WRITE),
        ("laptop_run_script", "Run one pre-registered, allow-listed named script.", ToolSensitivity.WRITE),
        ("laptop_shutdown", "Shut the laptop down.", ToolSensitivity.SENSITIVE),
    ]
    entries = {
        name: ToolManifestEntry(tool_name=name, server_key="laptop_agent", description=desc,
                                 sensitivity=tier, output_schema=LaptopActionRecord, rate_limit_per_minute=20)
        for name, desc, tier in laptop_specs
    }
    entries["remote_control_wake"] = ToolManifestEntry(
        tool_name="remote_control_wake", server_key="remote_control",
        description="Send a Wake-on-LAN packet to a registered device.",
        sensitivity=ToolSensitivity.WRITE, output_schema=WakeRecord, rate_limit_per_minute=10,
    )
    entries["remote_control_request_full_session"] = ToolManifestEntry(
        tool_name="remote_control_request_full_session", server_key="remote_control",
        description="Request full remote control (screen/mouse/keyboard) of a registered laptop. "
                     "The single most sensitive action in the system (build plan Section 7.1); "
                     "gated by top-trust-tier in addition to the ordinary SENSITIVE voice-gate.",
        sensitivity=ToolSensitivity.SENSITIVE, output_schema=FullControlHandoffRecord, rate_limit_per_minute=5,
    )
    ha_specs = [
        ("ha_lock", "Lock a Home Assistant lock entity.", ToolSensitivity.SENSITIVE),
        ("ha_unlock", "Unlock a Home Assistant lock entity.", ToolSensitivity.SENSITIVE),
        ("ha_turn_on", "Turn on a light or switch entity.", ToolSensitivity.WRITE),
        ("ha_turn_off", "Turn off a light or switch entity.", ToolSensitivity.WRITE),
        ("ha_get_state", "Read a Home Assistant entity's current state.", ToolSensitivity.READ_ONLY),
    ]
    for name, desc, tier in ha_specs:
        entries[name] = ToolManifestEntry(tool_name=name, server_key="home_assistant", description=desc,
                                           sensitivity=tier, output_schema=HaEntityStateRecord, rate_limit_per_minute=30)
    return entries


def build_registry() -> Dict[str, ToolManifestEntry]:
    """The real Phase 5 registry: Phase 4's real (non-demo) registry,
    unmodified, plus Phase 5's laptop_agent/remote_control/home_assistant
    tools."""
    registry = dict(build_phase4_registry())
    registry.update(_phase5_entries())
    return registry


def build_demo_registry() -> Dict[str, ToolManifestEntry]:
    """Adds Phase 3/4's demo fixtures on top of the real Phase 5
    registry, purely so older test scenarios keep working. Phase 5
    introduces no new demo-only fixture — see module docstring."""
    registry = dict(build_phase4_demo_registry())
    registry.update(_phase5_entries())
    return registry
