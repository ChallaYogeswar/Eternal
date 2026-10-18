#!/usr/bin/env python3
"""
Narrative walkthrough of Phase 5's exit criteria, run entirely in-process
— same style and same split as every earlier phase's demo_exit_criteria.py:
this script narrates the pipeline by calling components directly (real
QAP-P Orchestrator, real RCAP-P gateway, real fixture/simulated data
layers, real Wake-on-LAN bytes over a real loopback socket); the
over-the-wire, real-subprocess-MCP proof is the separate
scripts/test_mcp_e2e.py.

Eternal_Master_Build_Plan.md Phase 5 exit criterion:
    "full laptop control and a smart-home action both work from outside
    the home network, both require the phone-tap confirmation, and the
    kill switch has actually been tested."

"From outside the home network" is a Tailscale-mesh/network-topology
claim this sandbox cannot literally test (no second machine, no mesh) —
same category of untestable-here claim Phase 1's README already flagged
for its own Pi/offline-key deployment steps. What this script DOES prove,
directly: both actions require the phone-tap confirmation (never voice,
never low-trust), both work end-to-end when it is presented, and the
kill switch measurably changes real QAP-P/RCAP-P state, not just returns
ok=True.
"""
import asyncio
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from qap_p.config import Config as QapConfig
from qap_p.models import Device, DeviceRole
from qap_p.orchestrator import Orchestrator

from rcap_p.config import Config as RcapConfig
from rcap_p.gateway import RCAPGateway

from voice_consent_gateway.config import Config as Phase4Config
from voice_consent_gateway.consent import ConsentRegistry
from voice_consent_gateway.gateway import Phase4Gateway
from voice_consent_gateway.models import ConfirmationChannel, WriteCallRequest

from device_control_gateway.config import Config as Phase5Config
from device_control_gateway.gateway import FULL_CONTROL_TOOL_NAME, Phase5Gateway
from device_control_gateway.ledger_bridge import Ledger
from device_control_gateway.registry_ext import build_demo_registry
from device_control_gateway.trust_bridge import QapPTrustLookup
from device_control_gateway.trust_tier import TopTrustTierGate

from mcp_servers.laptop_agent import data as laptop_data
from mcp_servers.home_assistant import data as ha_data
from mcp_servers.remote_control import data as rc_data

PASS, FAIL = [], []


def check(label: str, condition: bool):
    (PASS if condition else FAIL).append(label)
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")


def section(title: str):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


async def main():
    print("Eternal Phase 5 (Remote Device Control + Home Assistant) — Exit Criteria Narrative Demo")
    print("Eternal_Master_Build_Plan.md Section 8, Phase 5")

    data_dir = tempfile.mkdtemp(prefix="phase5_demo_")

    qap_config = QapConfig(data_dir=data_dir)
    orchestrator = Orchestrator(qap_config)
    trust_lookup = QapPTrustLookup(orchestrator)

    ledger = Ledger(str(Path(data_dir) / "phase5_ledger.db"))
    registry = build_demo_registry()

    laptop_data.reset_provider_for_tests()
    ha_data.reset_client_for_tests()

    async def executor(tool_name: str, arguments: dict):
        """In-process executor calling the SAME data-layer modules the
        real MCP subprocess servers wrap — same simplification Phase 4's
        own demo used for aether, for the same reason (module docstrings
        of each mcp_servers/*/data.py file explain why the real
        subprocess proof lives in scripts/test_mcp_e2e.py instead)."""
        if tool_name == "laptop_lock":
            return laptop_data.get_provider().lock().as_dict()
        if tool_name == "laptop_unlock":
            return laptop_data.get_provider().unlock().as_dict()
        if tool_name == "laptop_sleep":
            return laptop_data.get_provider().sleep().as_dict()
        if tool_name == "laptop_open_app":
            return laptop_data.get_provider().open_app(arguments["app_name"]).as_dict()
        if tool_name == "laptop_run_script":
            return laptop_data.get_provider().run_script(arguments["script_name"]).as_dict()
        if tool_name == "laptop_shutdown":
            return laptop_data.get_provider().shutdown().as_dict()
        if tool_name == "ha_lock":
            return ha_data.get_client().call_service("lock", "lock", arguments["entity_id"]).as_dict()
        if tool_name == "ha_unlock":
            return ha_data.get_client().call_service("lock", "unlock", arguments["entity_id"]).as_dict()
        if tool_name == "ha_get_state":
            return ha_data.get_client().get_state(arguments["entity_id"]).as_dict()
        if tool_name == "remote_control_wake":
            target = rc_data.TARGET_DEVICES[arguments.get("device_label", "primary_laptop")]
            return rc_data.send_wol(target.mac_address, target_ip="127.0.0.1", port=WOL_TEST_PORT).as_dict()
        if tool_name == "remote_control_request_full_session":
            return rc_data.request_full_control(arguments.get("device_label", "primary_laptop"),
                                                  target_ip="127.0.0.1", port=WOL_TEST_PORT).as_dict()
        raise RuntimeError(f"demo executor has no case for tool {tool_name!r}")

    rcap_gateway = RCAPGateway(RcapConfig(data_dir=data_dir), registry, ledger, executor, trust_lookup=trust_lookup)
    consent_registry = ConsentRegistry(str(Path(data_dir) / "consent.db"), ledger)
    phase4_gateway = Phase4Gateway(rcap_gateway, registry, consent_registry, ledger)
    phase5_config = Phase5Config(data_dir=data_dir)
    top_trust_tier_gate = TopTrustTierGate(phase5_config, trust_lookup)
    gateway = Phase5Gateway(phase4_gateway, top_trust_tier_gate, orchestrator, ledger)

    # A real UDP listener standing in for "the target laptop's network
    # card, listening for a magic packet" — bound before any WoL is sent
    # so the real bytes-received assertion below isn't a race.
    wol_listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    wol_listener.bind(("127.0.0.1", 0))
    wol_listener.settimeout(2.0)
    WOL_TEST_PORT = wol_listener.getsockname()[1]
    wol_received = {}

    def _listen_for_wol():
        try:
            data, _addr = wol_listener.recvfrom(2048)
            wol_received["data"] = data
        except socket.timeout:
            wol_received["data"] = None

    # ------------------------------------------------------------------
    section("1. ENROLL A DEVICE — a freshly-enrolled session starts LOW TRUST, by design")
    # ------------------------------------------------------------------
    orchestrator.enroll_device("laptop-demo", DeviceRole.LAPTOP)
    fresh_session = orchestrator.create_session("laptop-demo")
    fresh_score = orchestrator.compute_trust_score("laptop-demo")
    print(f"    fresh session {fresh_session}, device trust score = {fresh_score:.2f}")
    check("a freshly-enrolled device's trust score is below the top-trust-tier threshold",
          fresh_score < phase5_config.top_trust_tier_min_score)

    # ------------------------------------------------------------------
    section("2. SMART-HOME ACTION — ha_unlock, a real (fixture-backed) Home Assistant lock")
    # ------------------------------------------------------------------
    consent_registry.grant("ha_unlock", granted_by="owner_interactive:demo-session")
    before_state = ha_data.get_client().get_state("lock.front_door")
    print(f"    lock.front_door BEFORE: {before_state.state}")
    check("front door starts locked in the fixture", before_state.state == "locked")

    voice_unlock_req = WriteCallRequest(
        tool_name="ha_unlock", arguments={"entity_id": "lock.front_door"}, session_id=fresh_session,
        confirmation_channel=ConfirmationChannel.VOICE, source_label="voice_pipeline",
        explanation="user said 'unlock the front door'",
    )
    voice_unlock_result = await gateway.call_tool(voice_unlock_req)
    check("ha_unlock over VOICE is denied (SENSITIVE tier, build plan Section 6.3 point 2)",
          voice_unlock_result.ok is False and "VOICE" in (voice_unlock_result.denied_reason or ""))
    after_voice_attempt = ha_data.get_client().get_state("lock.front_door")
    check("the door is STILL locked after the refused voice attempt — nothing silently happened",
          after_voice_attempt.state == "locked")

    tap_token = gateway.grant_tool_permission(fresh_session, "ha_unlock")
    tap_unlock_req = WriteCallRequest(
        tool_name="ha_unlock", arguments={"entity_id": "lock.front_door"}, session_id=fresh_session,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="phone_app",
        explanation="owner tapped 'Unlock front door' in the phone app after seeing the voice request",
        rotate_token=tap_token,
    )
    tap_unlock_result = await gateway.call_tool(tap_unlock_req)
    check("the SAME action succeeds once confirmed via a non-voice channel (phone-tap)", tap_unlock_result.ok)
    after_tap = ha_data.get_client().get_state("lock.front_door")
    check("lock.front_door is REALLY unlocked now, verified by reading it back — not just ok=True",
          after_tap.state == "unlocked")

    # ------------------------------------------------------------------
    section("3. LAPTOP FIXED-ACTION LIST — real state machine, fixed allow-lists")
    # ------------------------------------------------------------------
    consent_registry.grant("laptop_open_app", granted_by="owner_interactive:demo-session")
    consent_registry.grant("laptop_lock", granted_by="owner_interactive:demo-session")
    consent_registry.grant("laptop_sleep", granted_by="owner_interactive:demo-session")

    open_token = gateway.grant_tool_permission(fresh_session, "laptop_open_app")
    open_req = WriteCallRequest(
        tool_name="laptop_open_app", arguments={"app_name": "browser"}, session_id=fresh_session,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="phone_app",
        explanation="owner tapped 'open browser'", rotate_token=open_token,
    )
    open_result = await gateway.call_tool(open_req)
    check("opening an ALLOW-LISTED app succeeds", open_result.ok)

    bad_open_req = WriteCallRequest(
        tool_name="laptop_open_app", arguments={"app_name": "literally_anything"}, session_id=fresh_session,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="phone_app",
        explanation="testing the allow-list boundary", rotate_token=open_token,
    )
    bad_open_result = await gateway.call_tool(bad_open_req)
    check("opening a NON-allow-listed app is refused, not attempted", bad_open_result.ok is False)

    sleep_token = gateway.grant_tool_permission(fresh_session, "laptop_sleep")
    sleep_req = WriteCallRequest(
        tool_name="laptop_sleep", arguments={}, session_id=fresh_session,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="phone_app",
        explanation="owner tapped 'sleep laptop'", rotate_token=sleep_token,
    )
    sleep_result = await gateway.call_tool(sleep_req)
    check("laptop_sleep succeeds", sleep_result.ok)

    lock_token = gateway.grant_tool_permission(fresh_session, "laptop_lock")
    lock_after_sleep_req = WriteCallRequest(
        tool_name="laptop_lock", arguments={}, session_id=fresh_session,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="phone_app",
        explanation="trying to lock an already-sleeping machine", rotate_token=lock_token,
    )
    lock_after_sleep_result = await gateway.call_tool(lock_after_sleep_req)
    check("locking an ALREADY-SLEEPING machine correctly reports failure (real state machine, not a stub)",
          lock_after_sleep_result.ok is True and lock_after_sleep_result.content["ok"] is False)

    # ------------------------------------------------------------------
    section("4. FULL LAPTOP CONTROL — 'the single most sensitive action in the whole system'")
    # ------------------------------------------------------------------
    consent_registry.grant(FULL_CONTROL_TOOL_NAME, granted_by="owner_interactive:demo-session")

    low_trust_full_control_req = WriteCallRequest(
        tool_name=FULL_CONTROL_TOOL_NAME, arguments={"device_label": "primary_laptop"}, session_id=fresh_session,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="phone_app",
        explanation="testing full control at low trust",
    )
    low_trust_result = await gateway.call_tool(low_trust_full_control_req)
    check("full control is DENIED for a low-trust session even over the phone-tap channel",
          low_trust_result.ok is False and "trust score" in (low_trust_result.denied_reason or ""))
    check("full control denial happens BEFORE any Wake-on-LAN or handoff is attempted",
          low_trust_result.underlying_result is None)

    voice_full_control_req = WriteCallRequest(
        tool_name=FULL_CONTROL_TOOL_NAME, arguments={"device_label": "primary_laptop"}, session_id=fresh_session,
        confirmation_channel=ConfirmationChannel.VOICE, source_label="voice_pipeline",
        explanation="user said 'take over my laptop'",
    )
    voice_full_control_result = await gateway.call_tool(voice_full_control_req)
    check("full control is ALSO denied over voice (both gates independently refuse it)",
          voice_full_control_result.ok is False)

    print("    manufacturing a long-standing, no-quarantine-history device to clear the top-trust-tier bar...")
    trusted_device = Device(
        device_id="laptop-demo-trusted", role=DeviceRole.LAPTOP,
        public_key_pem="-----BEGIN PUBLIC KEY-----\ndemo\n-----END PUBLIC KEY-----",
        hardware_backed=True, attestation_type="demo-fixture-long-standing",
        enrolled_at=time.time() - 35 * 86400, quarantine_count=0, anomaly_count=0,
    )
    orchestrator.store.create_device(trusted_device)
    trusted_session = orchestrator.create_session("laptop-demo-trusted")
    trusted_score = orchestrator.compute_trust_score("laptop-demo-trusted")
    print(f"    trusted session {trusted_session}, device trust score = {trusted_score:.2f}")
    check("the manufactured device legitimately clears the top-trust-tier bar under qap_p's real formula",
          trusted_score >= phase5_config.top_trust_tier_min_score)

    t = threading.Thread(target=_listen_for_wol)
    t.start()
    full_control_token = gateway.grant_tool_permission(trusted_session, FULL_CONTROL_TOOL_NAME)
    granted_full_control_req = WriteCallRequest(
        tool_name=FULL_CONTROL_TOOL_NAME, arguments={"device_label": "primary_laptop"}, session_id=trusted_session,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="phone_app",
        explanation="owner tapped 'take over my laptop' after clearing top-trust-tier",
        rotate_token=full_control_token,
    )
    granted_full_control_result = await gateway.call_tool(granted_full_control_req)
    t.join(timeout=3.0)
    check("full control SUCCEEDS for a high-trust session via the phone-tap channel", granted_full_control_result.ok)
    if granted_full_control_result.ok:
        print(f"    handoff: {granted_full_control_result.content}")
    check("a REAL Wake-on-LAN magic packet was received by a real UDP listener (not simulated)",
          wol_received.get("data") == rc_data.build_magic_packet(rc_data.TARGET_DEVICES["primary_laptop"].mac_address))
    wol_listener.close()

    # ------------------------------------------------------------------
    section("5. KILL SWITCH — 'immediately quarantines every session and revokes every scoped token'")
    # ------------------------------------------------------------------
    sessions_before = {fresh_session, trusted_session}
    for sid in sessions_before:
        check(f"session {sid[:8]}... is ACTIVE before the kill switch",
              orchestrator.get_session_state(sid).value == "ACTIVE")

    report = gateway.pull_kill_switch(reason="demo_suspected_compromise")
    print(f"    devices_seen={report.devices_seen}, sessions_quarantined={len(report.sessions_quarantined)}, "
          f"vault_tokens_revoked={report.vault_tokens_revoked}, rotate_tokens_revoked={report.rotate_tokens_revoked}, "
          f"consent_revoked_for_tools={report.consent_revoked_for_tools}")

    check("every previously-ACTIVE session is now QUARANTINE",
          all(orchestrator.get_session_state(sid).value == "QUARANTINE" for sid in sessions_before))
    check("both demo sessions appear in the report's sessions_quarantined list",
          sessions_before.issubset(set(report.sessions_quarantined)))
    check("at least one ROTATE token was revoked (several were granted above)", report.rotate_tokens_revoked > 0)
    check("SENSITIVE-tier consent (ha_unlock, full-control) was revoked by the kill switch",
          "ha_unlock" in report.consent_revoked_for_tools and FULL_CONTROL_TOOL_NAME in report.consent_revoked_for_tools)
    check("WRITE-tier consent (laptop_lock) was NOT touched by the kill switch",
          "laptop_lock" not in report.consent_revoked_for_tools and consent_registry.has_consent("laptop_lock"))

    try:
        gateway.grant_tool_permission(fresh_session, "laptop_lock")
        regression_closed = False
    except ValueError as e:
        regression_closed = "not ACTIVE" in str(e)
    check("a quarantined session CANNOT be handed a fresh ROTATE token after the kill switch "
          "(regression test for the gap found live-testing this daemon — see device_control_gateway/gateway.py)",
          regression_closed)

    # ------------------------------------------------------------------
    section("6. LEDGER INTEGRITY — every event above lands in one tamper-evident hash chain")
    # ------------------------------------------------------------------
    intact, broken_id = ledger.verify_chain()
    check("ledger chain is intact after the full narrative", intact)
    check(f"ledger recorded a meaningful number of entries ({ledger.count()})", ledger.count() >= 10)

    conn = sqlite3.connect(str(Path(data_dir) / "phase5_ledger.db"))
    conn.execute("UPDATE ledger SET payload = '{\"tampered\": true}' WHERE id = 1")
    conn.commit()
    conn.close()
    intact_after_tamper, broken_id_after = ledger.verify_chain()
    check("tampering with a stored ledger row is detected by verify_chain()", not intact_after_tamper)
    print(f"    first broken entry after tampering: id={broken_id_after}")

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
