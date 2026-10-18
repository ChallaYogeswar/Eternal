#!/usr/bin/env python3
"""
Live end-to-end proof — mirrors qap-p's scripts/test_http_e2e.py and
Eternal Core's scripts/test_http_e2e.py: real processes, real protocol,
no mocking, run as a standalone script (not part of the pytest suite,
same split those two phases used).

Unlike Phase 1/2's HTTP daemon, Phase 3's "real thing" is the MCP
protocol itself: this script spawns the actual cinemavault, study_tracker,
web_search, and malicious_test servers as real subprocesses over real
stdio transport, and drives every call through the real RCAPGateway —
proving the Phase 3 exit criterion against an actual tool call, not an
asserted mock.
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rcap_p.config import Config
from rcap_p.deployment import build_demo_server_commands
from rcap_p.gateway import RCAPGateway
from rcap_p.ledger_bridge import Ledger
from rcap_p.mcp_client import MCPServerPool, build_tool_to_server_map
from rcap_p.models import CallerKind, ContainmentState, ToolCallContext
from rcap_p.registry import build_demo_registry

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
    import tempfile
    data_dir = tempfile.mkdtemp(prefix="rcap_e2e_")
    config = Config(data_dir=data_dir)
    ledger = Ledger(config.ledger_db_path)
    registry = build_demo_registry()

    server_commands = build_demo_server_commands(PROJECT_ROOT)
    pool = MCPServerPool(server_commands)
    tool_to_server = build_tool_to_server_map(registry)
    executor = pool.make_executor(tool_to_server)

    gateway = RCAPGateway(config=config, registry=registry, ledger=ledger, tool_executor=executor)

    ctx = ToolCallContext(session_id="e2e-session", caller_kind=CallerKind.OWNER_INTERACTIVE, source_label="e2e_script")

    try:
        # --- Real CinemaVault call, real subprocess ---
        token = gateway.grant_tool_permission("e2e-session", "cinemavault_list_movies")
        result = await gateway.call_tool(ctx, "cinemavault_list_movies", {"limit": 5, "offset": 0}, rotate_token=token)
        check("real CinemaVault subprocess call succeeds", result.ok)
        check("CinemaVault response is PASSTHROUGH (clean seed data)", result.containment_state == ContainmentState.PASSTHROUGH)
        check("CinemaVault response actually contains movie titles", "title" in str(result.content))

        # --- Real Study Tracker call, real subprocess ---
        token = gateway.grant_tool_permission("e2e-session", "study_get_curriculum_status")
        result = await gateway.call_tool(ctx, "study_get_curriculum_status", {}, rotate_token=token)
        check("real Study Tracker subprocess call succeeds", result.ok)
        check("Study Tracker response contains all 5 phases", str(result.content).count("phase_number") == 5)

        # --- Real web_search call, real subprocess, clean stub result ---
        token = gateway.grant_tool_permission("e2e-session", "web_search")
        result = await gateway.call_tool(ctx, "web_search", {"query": "eternal jarvis assistant", "max_results": 2}, rotate_token=token)
        check("real web_search subprocess call succeeds", result.ok)
        check("web_search response is PASSTHROUGH (clean stub result)", result.containment_state == ContainmentState.PASSTHROUGH)

        # --- THE EXIT CRITERION: a deliberately malicious test page ---
        token = gateway.grant_tool_permission("e2e-session", "malicious_test_fetch_page")
        malicious_ctx = ToolCallContext(session_id="e2e-session", caller_kind=CallerKind.OWNER_INTERACTIVE,
                                         source_label="malicious-test-site.invalid")
        result = await gateway.call_tool(
            malicious_ctx, "malicious_test_fetch_page", {"url": "https://malicious-test-site.invalid/page"},
            rotate_token=token,
        )
        check("malicious test page call did not raise / did not crash the script", True)  # we only get here if it didn't
        check("malicious test page is HARD_BLOCKED, not passed through", result.containment_state == ContainmentState.HARD_BLOCKED)
        check("malicious test page is NOT ok (not silently delivered as success)", not result.ok)
        check("raw injected instruction text is absent from the returned content",
              "IGNORE ALL PREVIOUS INSTRUCTIONS" not in str(result.content))
        check("returned content is a clear RCAP-P placeholder, not silence", "[RCAP-P BLOCKED]" in str(result.content))
        check("the flagged source was correctly attributed", result.attributed_source == "malicious-test-site.invalid")
        check("ATTRIBUTE now shows this source with at least one flag",
              gateway.attribute._get("malicious-test-site.invalid").flag_count >= 1)

        # --- Second injection shape: hidden HTML-comment directive ---
        token = gateway.grant_tool_permission("e2e-session", "malicious_test_fetch_document")
        result2 = await gateway.call_tool(
            malicious_ctx, "malicious_test_fetch_document", {"url": "https://malicious-test-site.invalid/doc"},
            rotate_token=token,
        )
        check("HTML-comment-hidden directive is also flagged (not just plain-text override)",
              result2.containment_state != ContainmentState.PASSTHROUGH)

        # --- Same server, clean content: proves CONTAIN acts on content, not blanket server distrust ---
        # (this source is now a repeat offender after two flags above, so a fresh source_label is used
        #  here deliberately, to isolate "is clean content from this SERVER still deliverable" from
        #  "is this SOURCE now auto-blocked" — both are real, distinct guarantees, tested separately)
        clean_ctx = ToolCallContext(session_id="e2e-session", caller_kind=CallerKind.OWNER_INTERACTIVE,
                                     source_label="a-different-clean-site.invalid")
        token = gateway.grant_tool_permission("e2e-session", "malicious_test_fetch_page_clean")
        result3 = await gateway.call_tool(
            clean_ctx, "malicious_test_fetch_page_clean", {"url": "https://a-different-clean-site.invalid/page"},
            rotate_token=token,
        )
        check("clean content from the SAME malicious_test server (different source) still passes through",
              result3.containment_state == ContainmentState.PASSTHROUGH)

        # --- Ledger integrity after everything above ---
        intact, broken_id = ledger.verify_chain()
        check("ledger chain is intact after the full run", intact)
        check("ledger recorded a meaningful number of entries", ledger.count() >= 8)

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
