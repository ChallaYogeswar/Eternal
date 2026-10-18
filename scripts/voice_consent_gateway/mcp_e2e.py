#!/usr/bin/env python3
"""
Live end-to-end proof — mirrors rcap-p's scripts/test_mcp_e2e.py (Phase 3):
real processes, real stdio MCP transport, no mocking, run as a standalone
script rather than part of the pytest suite (same split Phase 1/2/3 each
used for their own "real thing" proof).

This script spawns the actual `aether` and `sensitive_demo` MCP servers as
real subprocesses, and drives every call through the full, composed
Phase4Gateway (voice gate -> consent -> the real, unmodified RCAPGateway
-> real subprocess) — proving the Phase 4 exit criterion against an
actual tool call over a real transport, not an asserted mock.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

RCAP_P_ROOT_ENV = "RCAP_P_PROJECT_ROOT"
import os
RCAP_P_ROOT = Path(os.environ.get(RCAP_P_ROOT_ENV, PROJECT_ROOT))

from rcap_p.config import Config as RcapConfig
from rcap_p.gateway import RCAPGateway
from rcap_p.ledger_bridge import Ledger as RcapLedger
from rcap_p.mcp_client import MCPServerPool, build_tool_to_server_map

from voice_consent_gateway.consent import ConsentRegistry
from voice_consent_gateway.deployment import build_demo_server_commands
from voice_consent_gateway.gateway import Phase4Gateway
from voice_consent_gateway.ledger_bridge import Ledger
from voice_consent_gateway.models import ConfirmationChannel, WriteCallRequest
from voice_consent_gateway.registry_ext import build_demo_registry

PASS = []
FAIL = []


def check(label: str, condition: bool):
    if condition:
        PASS.append(label)
        print(f"[PASS] {label}")
    else:
        FAIL.append(label)
        print(f"[FAIL] {label}")


async def main():
    data_dir = tempfile.mkdtemp(prefix="phase4_e2e_")
    ledger = Ledger(os.path.join(data_dir, "ledger.db"))
    registry = build_demo_registry()

    server_commands = build_demo_server_commands(RCAP_P_ROOT)
    pool = MCPServerPool(server_commands)
    tool_to_server = build_tool_to_server_map(registry)
    executor = pool.make_executor(tool_to_server)

    rcap_gateway = RCAPGateway(config=RcapConfig(data_dir=data_dir), registry=registry, ledger=ledger, tool_executor=executor)
    consent_registry = ConsentRegistry(os.path.join(data_dir, "consent.db"), ledger)
    gateway = Phase4Gateway(rcap_gateway, registry, consent_registry, ledger)

    session_id = "e2e-session"

    async def authorized_call(tool_name, arguments, channel=ConfirmationChannel.TEXT_TYPED, explanation="e2e test call"):
        consent_registry.grant(tool_name, granted_by="e2e_script")
        token = rcap_gateway.grant_tool_permission(session_id, tool_name)
        req = WriteCallRequest(
            tool_name=tool_name, arguments=arguments, session_id=session_id,
            confirmation_channel=channel, source_label="e2e_script",
            explanation=explanation, rotate_token=token,
        )
        return await gateway.call_tool(req)

    try:
        # --- Real ÆTHER writes, real subprocess, real SQLite file ---
        r = await authorized_call("aether_create_habit", {"title": "Morning Run", "category": "Exercise"})
        check("real aether_create_habit subprocess call succeeds", r.ok)
        check("created habit has the requested title", r.content["title"] == "Morning Run")

        r = await authorized_call(
            "aether_log_habit", {"habit_title": "Morning Run", "date_key": "today"},
            channel=ConfirmationChannel.VOICE,
            explanation="You said: 'log my morning run habit as done for today'.",
        )
        check("WRITE-tier ÆTHER tool succeeds over VOICE once consent+token are granted "
              "(exit criterion clause 1: 'a spoken command changes tracker data')", r.ok)
        check("habit streak incremented to 1 after logging", r.content["habit"]["current_streak"] == 1)

        r2 = await authorized_call(
            "aether_log_habit", {"habit_title": "Morning Run", "date_key": "today"},
            channel=ConfirmationChannel.VOICE,
        )
        check("logging the same habit twice same day is idempotent (already_logged_today)",
              r2.content["already_logged_today"] is True)

        r = await authorized_call("aether_add_expense", {"name": "Lunch", "amount": 200, "category": "food"})
        check("real aether_add_expense subprocess call succeeds", r.ok)
        check("expense amount round-trips correctly", r.content["amount"] == 200)

        r = await authorized_call("aether_log_workout", {"workout_type": "Cycling", "duration_min": 45})
        check("real aether_log_workout subprocess call succeeds", r.ok)

        r = await authorized_call("aether_log_activity", {"activity_type": "MOVIE", "title": "Interstellar"})
        check("real aether_log_activity subprocess call succeeds", r.ok)

        r = await authorized_call("aether_add_dream", {"title": "Flying over mountains", "content": "vivid"})
        check("real aether_add_dream subprocess call succeeds", r.ok)

        r = await authorized_call("aether_add_calendar_event", {"title": "Mom's Birthday", "event_type": "BIRTHDAY", "date": "2026-06-15"})
        check("real aether_add_calendar_event subprocess call succeeds", r.ok)

        r = await authorized_call("aether_add_memory", {"title": "First marathon", "content": "4:32", "emoji": "🏆"})
        check("real aether_add_memory subprocess call succeeds", r.ok)

        # --- THE EXIT CRITERION, second clause: SENSITIVE tier + VOICE ---
        consent_registry.grant("sensitive_demo_unlock_door", granted_by="e2e_script")
        token = rcap_gateway.grant_tool_permission(session_id, "sensitive_demo_unlock_door")
        voice_req = WriteCallRequest(
            tool_name="sensitive_demo_unlock_door", arguments={"door": "front"}, session_id=session_id,
            confirmation_channel=ConfirmationChannel.VOICE, source_label="e2e_script",
            explanation="You said: 'unlock the front door'.", rotate_token=token,
        )
        r = await gateway.call_tool(voice_req)
        check("SENSITIVE-tier call over VOICE did not crash the script", True)  # only reached if it didn't
        check("SENSITIVE-tier call over VOICE is denied even with consent+token already granted "
              "(exit criterion clause 2: 'no sensitive-tier action can complete on voice alone')", not r.ok)
        check("denial reason correctly names VOICE and SENSITIVE-tier", "VOICE" in r.denied_reason and "SENSITIVE" in r.denied_reason)
        check("the real sensitive_demo subprocess was never actually invoked",
              r.underlying_result is None)

        app_tap_req = WriteCallRequest(
            tool_name="sensitive_demo_unlock_door", arguments={"door": "front"}, session_id=session_id,
            confirmation_channel=ConfirmationChannel.APP_TAP, source_label="e2e_script",
            explanation="Owner tapped 'Unlock front door' in the phone app.", rotate_token=token,
        )
        r2 = await gateway.call_tool(app_tap_req)
        check("the SAME sensitive tool succeeds for real over APP_TAP (a genuine non-voice confirmation)", r2.ok)
        check("real sensitive_demo subprocess response is returned", "front" in str(r2.content))

        # --- Explanation logging, against the real ledger (full history,
        # not just the tail — a dozen-plus calls have happened by now) ---
        full_history = ledger.recent(ledger.count())
        explained = [e for e in full_history if e["event_type"] == "phase4_voice_gate_denied"]
        check("the voice-gate denial was logged with the caller's own explanation",
              any("unlock the front door" in e["payload"]["explanation"] for e in explained))

        write_explained = [e for e in full_history if e["event_type"] == "phase4_write_call_explained"]
        check("at least one successful write call was logged with an explanation",
              any(e["payload"]["ok"] and "morning run" in e["payload"]["explanation"] for e in write_explained))

        # --- Ledger integrity across the whole run ---
        intact, broken_id = ledger.verify_chain()
        check("Phase 4's ledger chain is intact after the full run", intact)
        check("ledger recorded a meaningful number of entries", ledger.count() >= 15)

    finally:
        await pool.aclose()

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
