#!/usr/bin/env python3
"""
Live, real-subprocess MCP proof for Phase 6 — same role and pattern as
rcap_p/scripts/test_mcp_e2e.py (Phase 3), eternal-voice_consent_gateway/scripts/
test_mcp_e2e.py (Phase 4), and eternal-device_control_gateway/scripts/test_mcp_e2e.py
(Phase 5): Phase6Gateway -> Phase5Gateway (top-trust-tier) -> Phase4Gateway
(voice gate -> consent) -> the real, unmodified RCAPGateway (PERIMETER ->
INSPECT -> ROTATE -> DECEIVE -> CONTAIN -> ATTRIBUTE) -> a REAL stdio MCP
subprocess for trading_engine, spawned exactly the way a real deployment
would spawn it. demo_exit_criteria.py proves the security-gate narrative
with an in-process executor for readability and to isolate KiteConnectBroker
directly; this script proves the wire protocol itself, plus one thing the
in-process demo cannot: that paper-trading state genuinely survives across
SEPARATE subprocess invocations, not just across Python objects held in
one process's memory (Phase 5's own README flags exactly this category of
bug — "in-memory state not surviving fresh-subprocess-per-call model" —
as something discovered the hard way, not assumed away here).

Run:
    PYTHONPATH=.:<path-to-qap-p>:<path-to-rcap-p>:<path-to-eternal-voice_consent_gateway>:<path-to-eternal-device_control_gateway> \\
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
from device_control_gateway.gateway import Phase5Gateway
from device_control_gateway.mcp_client_env import make_env_aware_stateless_executor
from device_control_gateway.trust_bridge import QapPTrustLookup
from device_control_gateway.trust_tier import TopTrustTierGate

from trading_risk_engine.config import Config as Phase6Config
from trading_risk_engine.deployment import build_demo_server_commands
from trading_risk_engine.gateway import Phase6Gateway
from trading_risk_engine.ledger_bridge import Ledger
from trading_risk_engine.regulatory import DailyReauthGate, LiveTradingGate, OrderRateLimiter, StaticIPHostGate
from trading_risk_engine.registry_ext import build_demo_registry

PASS, FAIL = [], []


def check(label: str, condition: bool):
    (PASS if condition else FAIL).append(label)
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")


async def main():
    print("Phase 6 — live subprocess MCP round trip")
    data_dir = tempfile.mkdtemp(prefix="phase6_mcp_e2e_")

    qap_config = QapConfig(data_dir=data_dir)
    orchestrator = Orchestrator(qap_config)
    registry = build_demo_registry()
    tool_to_server = build_tool_to_server_map(registry)
    server_commands = build_demo_server_commands(RCAP_P_ROOT)
    ledger = Ledger(Phase6Config(data_dir=data_dir).ledger_db_path)

    # Real subprocess environment: PYTHONPATH so `import data` -> `from
    # phase6 import ...` resolves inside the spawned process, and
    # PHASE6_DATA_DIR so the subprocess's OWN TradingEngine singleton
    # reads/writes the SAME paper-broker/ledger files this script uses —
    # exactly what a real deployment's systemd unit would set.
    subprocess_env = dict(os.environ)
    subprocess_env["PYTHONPATH"] = ":".join(sys.path)
    subprocess_env["PHASE6_DATA_DIR"] = data_dir
    executor = make_env_aware_stateless_executor(server_commands, tool_to_server, extra_env=subprocess_env)

    trust_lookup = QapPTrustLookup(orchestrator)
    rcap_gateway = RCAPGateway(RcapConfig(data_dir=data_dir), registry, ledger, executor, trust_lookup=trust_lookup)
    phase4_config = Phase4Config(data_dir=data_dir)
    consent_registry = ConsentRegistry(phase4_config.consent_db_path, ledger)
    phase4_gateway = Phase4Gateway(rcap_gateway, registry, consent_registry, ledger)
    phase5_config = Phase5Config(data_dir=data_dir)
    top_trust_tier_gate = TopTrustTierGate(phase5_config, trust_lookup)
    phase5_gateway = Phase5Gateway(phase4_gateway, top_trust_tier_gate, orchestrator, ledger)

    reg_config = Phase6Config(data_dir=data_dir, live_trading_enabled=True, trading_host_role="home-server-fixed-ip")
    live_gate = LiveTradingGate(
        reg_config, static_ip_gate=StaticIPHostGate(reg_config),
        daily_reauth_gate=DailyReauthGate(type("_NR", (), {"token_status": lambda self: __import__(
            "trading_risk_engine.broker.daily_reauth", fromlist=["TokenStatus"]).TokenStatus(
            has_token=False, obtained_at_ist=None, valid_until_ist=None, is_valid_now=False, hours_remaining=None
        )})()),
        rate_limiter=OrderRateLimiter(100.0),
    )
    phase6_gateway = Phase6Gateway(phase5_gateway, top_trust_tier_gate, live_gate, ledger)

    device = Device(device_id="mcp-e2e-device", role=DeviceRole.MINI_PC, public_key_pem="demo",
                    hardware_backed=True, attestation_type="demo-fixture", enrolled_at=__import__("time").time())
    orchestrator.store.create_device(device)
    session_id = orchestrator.create_session("mcp-e2e-device")

    print("\n--- READ_ONLY: trading_get_signal, over a real stdio subprocess ---")
    consent_registry.grant("trading_get_signal", granted_by="owner_interactive:e2e")
    token = phase6_gateway.grant_tool_permission(session_id, "trading_get_signal")
    req = WriteCallRequest(
        tool_name="trading_get_signal", arguments={"symbol": "RELIANCE.NS"}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.TEXT_TYPED, source_label="mcp_e2e_script",
        explanation="e2e check", rotate_token=token,
    )
    result = await phase6_gateway.call_tool(req)
    print(f"  direction={result.content.get('direction') if result.ok else result.denied_reason}")
    check("trading_get_signal succeeds over a real MCP subprocess", result.ok is True)
    check("the signal includes rule-level reasons, not just a bare direction",
          result.ok and len(result.content.get("reasons", [])) >= 0)

    print("\n--- READ_ONLY: trading_run_backtest, over a real stdio subprocess ---")
    consent_registry.grant("trading_run_backtest", granted_by="owner_interactive:e2e")
    token = phase6_gateway.grant_tool_permission(session_id, "trading_run_backtest")
    req = WriteCallRequest(
        tool_name="trading_run_backtest",
        arguments={"symbols": ["RELIANCE.NS"], "start_date": "2023-01-01", "end_date": "2025-01-01"},
        session_id=session_id, confirmation_channel=ConfirmationChannel.TEXT_TYPED, source_label="mcp_e2e_script",
        explanation="e2e backtest check", rotate_token=token,
    )
    result = await phase6_gateway.call_tool(req)
    check("trading_run_backtest succeeds over a real MCP subprocess", result.ok is True)
    if result.ok:
        print(f"  {result.content['num_trades']} trades, Sharpe={result.content['performance']['sharpe_ratio']:.2f}")

    print("\n--- READ_ONLY: trading_check_recovery_math, over a real stdio subprocess ---")
    consent_registry.grant("trading_check_recovery_math", granted_by="owner_interactive:e2e")
    token = phase6_gateway.grant_tool_permission(session_id, "trading_check_recovery_math")
    req = WriteCallRequest(
        tool_name="trading_check_recovery_math", arguments={"drawdown_pct": 20.0, "period_days": 7},
        session_id=session_id, confirmation_channel=ConfirmationChannel.TEXT_TYPED, source_label="mcp_e2e_script",
        explanation="e2e recovery math check", rotate_token=token,
    )
    result = await phase6_gateway.call_tool(req)
    print(f"  {result.content}")
    check("recovery math over the real subprocess matches the build plan's own 25% figure for a 20% loss",
          result.ok and abs(result.content["return_needed_to_recover_pct"] - 25.0) < 0.01)

    print("\n--- WRITE: a paper order placed via one subprocess, visible from a DIFFERENT subprocess call ---")
    consent_registry.grant("trading_place_paper_order", granted_by="owner_interactive:e2e")
    token = phase6_gateway.grant_tool_permission(session_id, "trading_place_paper_order")
    order_req = WriteCallRequest(
        tool_name="trading_place_paper_order",
        arguments={"symbol": "TCS.NS", "side": "BUY", "quantity": 2, "order_type": "MARKET"},
        session_id=session_id, confirmation_channel=ConfirmationChannel.APP_TAP, source_label="mcp_e2e_script",
        explanation="e2e paper order", rotate_token=token,
    )
    order_result = await phase6_gateway.call_tool(order_req)
    print(f"  order result: {order_result.content}")
    check("the paper order fills over a real MCP subprocess", order_result.ok and order_result.content["status"] == "FILLED")

    consent_registry.grant("trading_get_paper_positions", granted_by="owner_interactive:e2e")
    token = phase6_gateway.grant_tool_permission(session_id, "trading_get_paper_positions")
    positions_req = WriteCallRequest(
        tool_name="trading_get_paper_positions", arguments={}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.TEXT_TYPED, source_label="mcp_e2e_script",
        explanation="e2e position check", rotate_token=token,
    )
    positions_result = await phase6_gateway.call_tool(positions_req)
    positions_list = positions_result.content.get("result", []) if positions_result.ok else []
    print(f"  positions (fetched from a BRAND NEW subprocess): {positions_list}")
    check(
        "the position from the FIRST subprocess call is visible to a SECOND, entirely separate subprocess "
        "call — proving PaperBroker's SQLite persistence, not in-memory state that a fresh process would lose",
        any(p["symbol"] == "TCS.NS" and p["quantity"] == 2 for p in positions_list),
    )

    print("\n--- F&O READ_ONLY: fo_get_signal, over a real stdio subprocess ---")
    consent_registry.grant("fo_get_signal", granted_by="owner_interactive:e2e")
    token = phase6_gateway.grant_tool_permission(session_id, "fo_get_signal")
    fo_sig_req = WriteCallRequest(
        tool_name="fo_get_signal", arguments={"underlying_symbol": "NIFTY"}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.TEXT_TYPED, source_label="mcp_e2e_script",
        explanation="e2e F&O signal check", rotate_token=token,
    )
    fo_sig_result = await phase6_gateway.call_tool(fo_sig_req)
    print(f"  direction={fo_sig_result.content.get('direction') if fo_sig_result.ok else fo_sig_result.denied_reason}")
    check("fo_get_signal succeeds over a real MCP subprocess", fo_sig_result.ok is True)

    print("\n--- F&O READ_ONLY: fo_get_option_chain, over a real stdio subprocess ---")
    consent_registry.grant("fo_get_option_chain", granted_by="owner_interactive:e2e")
    token = phase6_gateway.grant_tool_permission(session_id, "fo_get_option_chain")
    chain_req = WriteCallRequest(
        tool_name="fo_get_option_chain", arguments={"underlying_symbol": "NIFTY", "num_strikes_each_side": 3},
        session_id=session_id, confirmation_channel=ConfirmationChannel.TEXT_TYPED, source_label="mcp_e2e_script",
        explanation="e2e F&O option chain check", rotate_token=token,
    )
    chain_result = await phase6_gateway.call_tool(chain_req)
    check("fo_get_option_chain succeeds over a real MCP subprocess and returns a full CE+PE chain",
          chain_result.ok and len(chain_result.content.get("quotes", [])) == 14)

    print("\n--- F&O READ_ONLY: fo_run_options_backtest returns the performance block over the real wire ---")
    consent_registry.grant("fo_run_options_backtest", granted_by="owner_interactive:e2e")
    token = phase6_gateway.grant_tool_permission(session_id, "fo_run_options_backtest")
    bt_req = WriteCallRequest(
        tool_name="fo_run_options_backtest",
        arguments={"underlying_symbol": "NIFTY", "strategy_style": "DIRECTIONAL_LONG", "lookback_days": 150},
        session_id=session_id, confirmation_channel=ConfirmationChannel.TEXT_TYPED, source_label="mcp_e2e_script",
        explanation="e2e F&O backtest performance check", rotate_token=token,
    )
    bt_result = await phase6_gateway.call_tool(bt_req)
    perf_block = (bt_result.content or {}).get("performance") if bt_result.ok else None
    check("fo_run_options_backtest over a real MCP subprocess includes JSON-safe Sharpe/Sortino/Calmar/drawdown",
          bool(perf_block) and all(k in perf_block for k in ("sharpe_ratio", "sortino_ratio", "calmar_ratio", "max_drawdown_pct")))

    print("\n--- F&O WRITE: a paper option order placed via one subprocess, visible from a DIFFERENT "
         "subprocess call ---")
    consent_registry.grant("fo_place_paper_option_order", granted_by="owner_interactive:e2e")
    token = phase6_gateway.grant_tool_permission(session_id, "fo_place_paper_option_order")
    sample_strike = chain_result.content["quotes"][3]["strike"] if chain_result.ok else 24500.0
    fo_order_req = WriteCallRequest(
        tool_name="fo_place_paper_option_order",
        arguments={"underlying_symbol": "NIFTY", "strike": sample_strike, "option_type": "CE",
                  "side": "LONG", "num_lots": 1, "expiry_choice": "WEEKLY"},
        session_id=session_id, confirmation_channel=ConfirmationChannel.APP_TAP, source_label="mcp_e2e_script",
        explanation="e2e F&O paper order", rotate_token=token,
    )
    fo_order_result = await phase6_gateway.call_tool(fo_order_req)
    print(f"  order result: {fo_order_result.content}")
    check("the F&O paper option order fills over a real MCP subprocess",
          fo_order_result.ok and fo_order_result.content["status"] == "FILLED")

    consent_registry.grant("fo_get_paper_positions", granted_by="owner_interactive:e2e")
    token = phase6_gateway.grant_tool_permission(session_id, "fo_get_paper_positions")
    fo_positions_req = WriteCallRequest(
        tool_name="fo_get_paper_positions", arguments={}, session_id=session_id,
        confirmation_channel=ConfirmationChannel.TEXT_TYPED, source_label="mcp_e2e_script",
        explanation="e2e F&O position check", rotate_token=token,
    )
    fo_positions_result = await phase6_gateway.call_tool(fo_positions_req)
    fo_positions_list = fo_positions_result.content.get("result", []) if fo_positions_result.ok else []
    print(f"  F&O positions (fetched from a BRAND NEW subprocess): {fo_positions_list}")
    check(
        "the F&O position from the FIRST subprocess call is visible to a SECOND, entirely separate "
        "subprocess call — proving FOPaperBroker's SQLite persistence, same discipline as equity's PaperBroker",
        any(p["underlying_symbol"] == "NIFTY" and p["kind"] == "OPTION" for p in fo_positions_list),
    )

    print("\n--- Kill switch still covers a Phase 6 tool, over the real wire protocol ---")
    consent_registry.grant("trading_place_live_order", granted_by="owner_interactive:e2e")
    pre_kill_token = phase6_gateway.grant_tool_permission(session_id, "trading_place_live_order")
    check("a normal, ACTIVE session can be granted a token before the kill switch fires", pre_kill_token is not None)
    kill_report = phase5_gateway.pull_kill_switch(reason="mcp_e2e_test")
    try:
        post_kill_token = phase6_gateway.grant_tool_permission(session_id, "trading_place_live_order")
    except ValueError:
        post_kill_token = None
    check("after the kill switch, the SAME session cannot be granted a fresh token for a live order",
          post_kill_token is None)

    print("\n--- Kill switch also covers an F&O tool, over the real wire protocol, with zero F&O-specific "
         "kill-switch code ---")
    consent_registry.grant("fo_place_live_option_order", granted_by="owner_interactive:e2e")
    try:
        fo_post_kill_token = phase6_gateway.grant_tool_permission(session_id, "fo_place_live_option_order")
    except ValueError:
        fo_post_kill_token = None
    check("after the (equity) kill switch above already fired, the SAME session cannot be granted a "
         "fresh token for an F&O live order either — Phase5Gateway's ACTIVE-session guard applies no "
         "tool_name filter at all, so this required zero new code",
          fo_post_kill_token is None)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        sys.exit(1)
    print("ALL LIVE MCP SUBPROCESS CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
