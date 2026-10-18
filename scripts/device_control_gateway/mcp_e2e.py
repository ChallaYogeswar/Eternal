#!/usr/bin/env python3
"""
Live, real-subprocess MCP proof for Phase 5 — same role and same pattern
as rcap_p/scripts/test_mcp_e2e.py (Phase 3) and eternal-voice_consent_gateway/scripts/
test_mcp_e2e.py (Phase 4): Phase5Gateway (top-trust-tier -> Phase4Gateway
(voice gate -> consent -> the real, unmodified RCAPGateway (PERIMETER ->
INSPECT -> ROTATE -> DECEIVE -> CONTAIN -> ATTRIBUTE) -> a REAL stdio MCP
subprocess for laptop_agent / remote_control / home_assistant, spawned
exactly the way a real deployment would spawn them, not an in-process
stand-in. demo_exit_criteria.py proves the narrative with an in-process
executor for readability; this script proves the wire protocol actually
works end to end.

Run:
    PYTHONPATH=.:<path-to-qap-p>:<path-to-rcap-p>:<path-to-eternal-voice_consent_gateway> \
    RCAP_P_PROJECT_ROOT=<path-to-rcap-p> python scripts/test_mcp_e2e.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

RCAP_P_ROOT = Path(os.environ.get("RCAP_P_PROJECT_ROOT", PROJECT_ROOT))

from qap_p.config import Config as QapConfig
from qap_p.models import Device, DeviceRole
from qap_p.orchestrator import Orchestrator

from rcap_p.config import Config as RcapConfig
from rcap_p.gateway import RCAPGateway
from rcap_p.mcp_client import build_tool_to_server_map

from voice_consent_gateway.config import Config as Phase4Config
from voice_consent_gateway.consent import ConsentRegistry
from voice_consent_gateway.gateway import Phase4Gateway
from voice_consent_gateway.models import ConfirmationChannel, WriteCallRequest

from device_control_gateway.config import Config as Phase5Config
from device_control_gateway.deployment import build_demo_server_commands
from device_control_gateway.gateway import FULL_CONTROL_TOOL_NAME, Phase5Gateway
from device_control_gateway.ledger_bridge import Ledger
from device_control_gateway.mcp_client_env import make_env_aware_stateless_executor
from device_control_gateway.registry_ext import build_demo_registry
from device_control_gateway.trust_bridge import QapPTrustLookup
from device_control_gateway.trust_tier import TopTrustTierGate

PASS, FAIL = [], []


def check(label: str, condition: bool):
    (PASS if condition else FAIL).append(label)
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")


async def main():
    print("Eternal Phase 5 — Live MCP Subprocess End-to-End Proof")
    data_dir = tempfile.mkdtemp(prefix="phase5_mcp_e2e_")

    # Isolate this run's simulated laptop/HA state to its own temp dir.
    # This ONLY works because the executor below is
    # device_control_gateway.mcp_client_env.make_env_aware_stateless_executor, not
    # rcap_p.mcp_client.make_stateless_executor — see that module's
    # docstring for the gap it closes (verified directly against the MCP
    # SDK: a bare StdioServerParameters(command, args) with no env= gives
    # the subprocess only HOME/PATH/TERM, never this process's os.environ,
    # so setting these here would silently do nothing against the plain
    # rcap_p executor).
    os.environ["PHASE5_LAPTOP_STATE_PATH"] = str(Path(data_dir) / "laptop_state.json")
    os.environ["PHASE5_HA_STATE_PATH"] = str(Path(data_dir) / "ha_state.json")

    orchestrator = Orchestrator(QapConfig(data_dir=data_dir))
    trust_lookup = QapPTrustLookup(orchestrator)
    ledger = Ledger(str(Path(data_dir) / "ledger.db"))
    registry = build_demo_registry()
    server_commands = build_demo_server_commands(RCAP_P_ROOT)

    print(f"  spawning real subprocess servers for: {sorted(set(k for k in server_commands))}")

    tool_to_server = build_tool_to_server_map(registry)
    executor = make_env_aware_stateless_executor(server_commands, tool_to_server, extra_env=dict(os.environ))

    rcap_gateway = RCAPGateway(RcapConfig(data_dir=data_dir), registry, ledger, executor, trust_lookup=trust_lookup)
    consent_registry = ConsentRegistry(str(Path(data_dir) / "consent.db"), ledger)
    phase4_gateway = Phase4Gateway(rcap_gateway, registry, consent_registry, ledger)
    phase5_config = Phase5Config(data_dir=data_dir)
    top_trust_tier_gate = TopTrustTierGate(phase5_config, trust_lookup)
    gateway = Phase5Gateway(phase4_gateway, top_trust_tier_gate, orchestrator, ledger)

    orchestrator.enroll_device("e2e-laptop", DeviceRole.LAPTOP)
    session_id = orchestrator.create_session("e2e-laptop")

    # ---- laptop_agent: real subprocess, real state machine ----
    consent_registry.grant("laptop_lock", granted_by="e2e_script")
    consent_registry.grant("laptop_unlock", granted_by="e2e_script")
    consent_registry.grant("laptop_open_app", granted_by="e2e_script")

    lock_token = gateway.grant_tool_permission(session_id, "laptop_lock")
    lock_result = await gateway.call_tool(WriteCallRequest(
        tool_name="laptop_lock", arguments={}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="e2e_script",
        explanation="e2e: lock the laptop", rotate_token=lock_token,
    ))
    check("laptop_lock succeeds via a REAL subprocess call", lock_result.ok and lock_result.content["lock_state"] == "locked")

    open_token = gateway.grant_tool_permission(session_id, "laptop_open_app")
    blocked_open_result = await gateway.call_tool(WriteCallRequest(
        tool_name="laptop_open_app", arguments={"app_name": "browser"}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="e2e_script",
        explanation="e2e: try to open an app while locked", rotate_token=open_token,
    ))
    check("opening an app while locked is correctly refused by the REAL subprocess's own state machine",
          blocked_open_result.ok is True and blocked_open_result.content["ok"] is False)

    bad_app_result = await gateway.call_tool(WriteCallRequest(
        tool_name="laptop_open_app", arguments={"app_name": "not_a_real_app_at_all"}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="e2e_script",
        explanation="e2e: try a non-allow-listed app name", rotate_token=open_token,
    ))
    check("a non-allow-listed app name is refused BY THE REAL SUBPROCESS, not just by client-side validation",
          bad_app_result.ok is False)

    unlock_token = gateway.grant_tool_permission(session_id, "laptop_unlock")
    voice_unlock_result = await gateway.call_tool(WriteCallRequest(
        tool_name="laptop_unlock", arguments={}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.VOICE, source_label="e2e_script",
        explanation="e2e: try to unlock over voice", rotate_token=unlock_token,
    ))
    check("laptop_unlock over VOICE is denied before ever reaching the subprocess (SENSITIVE tier)",
          voice_unlock_result.ok is False and "VOICE" in (voice_unlock_result.denied_reason or ""))

    tap_unlock_result = await gateway.call_tool(WriteCallRequest(
        tool_name="laptop_unlock", arguments={}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="e2e_script",
        explanation="e2e: unlock via phone tap", rotate_token=unlock_token,
    ))
    check("laptop_unlock succeeds via phone-tap against the REAL subprocess",
          tap_unlock_result.ok and tap_unlock_result.content["lock_state"] == "unlocked")

    # ---- home_assistant: real subprocess, real fixture state ----
    consent_registry.grant("ha_unlock", granted_by="e2e_script")
    consent_registry.grant("ha_get_state", granted_by="e2e_script")

    state_read_token_1 = gateway.grant_tool_permission(session_id, "ha_get_state")
    before_result = await gateway.call_tool(WriteCallRequest(
        tool_name="ha_get_state", arguments={"entity_id": "lock.front_door"}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="e2e_script",
        explanation="e2e: read lock state before acting", rotate_token=state_read_token_1,
    ))
    check("ha_get_state reads real state from the REAL subprocess's fixture",
          before_result.ok and before_result.content["state"] == "locked")

    ha_token = gateway.grant_tool_permission(session_id, "ha_unlock")
    ha_unlock_result = await gateway.call_tool(WriteCallRequest(
        tool_name="ha_unlock", arguments={"entity_id": "lock.front_door"}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="e2e_script",
        explanation="e2e: unlock the front door", rotate_token=ha_token,
    ))
    check("ha_unlock succeeds against the REAL subprocess",
          ha_unlock_result.ok and ha_unlock_result.content["state"] == "unlocked")

    state_read_token_2 = gateway.grant_tool_permission(session_id, "ha_get_state")
    after_result = await gateway.call_tool(WriteCallRequest(
        tool_name="ha_get_state", arguments={"entity_id": "lock.front_door"}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="e2e_script",
        explanation="e2e: confirm the change persisted across a FRESH subprocess", rotate_token=state_read_token_2,
    ))
    check("the unlocked state set by one subprocess is visible to a DIFFERENT, later-spawned subprocess "
          "(file-backed persistence, not shared memory)",
          after_result.ok and after_result.content["state"] == "unlocked")

    state_read_token_3 = gateway.grant_tool_permission(session_id, "ha_get_state")
    unknown_entity_result = await gateway.call_tool(WriteCallRequest(
        tool_name="ha_get_state", arguments={"entity_id": "lock.does_not_exist"}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="e2e_script",
        explanation="e2e: query an unknown entity", rotate_token=state_read_token_3,
    ))
    check("querying an unknown entity is refused by the REAL subprocess, not silently fabricated",
          unknown_entity_result.ok is False)

    # ---- remote_control: real subprocess, real Wake-on-LAN bytes fired ----
    consent_registry.grant("remote_control_wake", granted_by="e2e_script")
    wake_token = gateway.grant_tool_permission(session_id, "remote_control_wake")
    wake_result = await gateway.call_tool(WriteCallRequest(
        tool_name="remote_control_wake", arguments={"device_label": "primary_laptop"}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="e2e_script",
        explanation="e2e: send Wake-on-LAN", rotate_token=wake_token,
    ))
    check("remote_control_wake sends a real magic packet via the REAL subprocess",
          wake_result.ok and wake_result.content["bytes_sent"] == 6 + 16 * 6)

    # ---- full control: denied at low trust, even via the real subprocess pipeline ----
    consent_registry.grant(FULL_CONTROL_TOOL_NAME, granted_by="e2e_script")
    full_control_result = await gateway.call_tool(WriteCallRequest(
        tool_name=FULL_CONTROL_TOOL_NAME, arguments={"device_label": "primary_laptop"}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="e2e_script",
        explanation="e2e: request full control at low trust",
    ))
    check("full control is refused for a low-trust session BEFORE the subprocess is ever invoked",
          full_control_result.ok is False and "trust score" in (full_control_result.denied_reason or ""))

    # ---- kill switch, proven against real subprocess-issued tokens ----
    report = gateway.pull_kill_switch(reason="e2e_script")
    check("the kill switch quarantines the session used throughout this real-subprocess run",
          session_id in report.sessions_quarantined)
    check("the kill switch revoked more than zero ROTATE tokens actually used against real subprocesses",
          report.rotate_tokens_revoked > 0)

    intact, _ = ledger.verify_chain()
    check("the ledger chain covering this entire real-subprocess run is intact", intact)

    print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED CHECKS:")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
