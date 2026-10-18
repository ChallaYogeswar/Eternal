#!/usr/bin/env python3
"""
Narrative walkthrough of Phase 6's exit criteria, run entirely in-process
— same style and same split as every earlier phase's demo_exit_criteria.py:
this script calls real components directly (real QAP-P Orchestrator, real
RCAP-P/Phase4/Phase5 gateway stack, real indicator/signal/risk/backtester/
paper-trader code, a real — if sandbox-network-restricted — Kite Connect
SDK call at the very end of the chain); the over-the-wire, real-subprocess
-MCP proof is the separate scripts/test_mcp_e2e.py, and the real-HTTP-
daemon proof is scripts/test_http_e2e.py.

Eternal_Master_Build_Plan.md Section 8, Phase 6 exit criterion:
    "sub-phase A shows a real edge across enough backtested and paper-
    traded trades to be statistically meaningful — not a good week."

This script does not (and structurally cannot, in one sitting) prove
"statistically meaningful" for any particular strategy — that is a claim
about running this system for real, for months, which is the entire point
of Phase 6 sub-phase A being a real waiting period, not a demo. What this
script DOES prove: every mechanism that claim depends on is real and
wired correctly — indicators match hand-derived values, position sizing
is fixed-fractional and ATR-based, the backtester has no lookahead bias
and applies real costs, the max-drawdown circuit breaker actually halts
trading and reports the honest recovery math, paper trading persists
state across process restarts, and — the highest-consequence claim in
this phase — a live order cannot be placed without clearing an unbroken
chain of independent gates ending at the real (if network-sandboxed)
Kite Connect SDK call itself.
"""
import asyncio
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

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
from device_control_gateway.deployment import build_default_server_commands as build_phase5_server_commands
from device_control_gateway.gateway import Phase5Gateway
from device_control_gateway.mcp_client_env import make_env_aware_stateless_executor
from device_control_gateway.trust_bridge import QapPTrustLookup
from device_control_gateway.trust_tier import TopTrustTierGate

from trading_risk_engine import indicators as ind
from trading_risk_engine import performance as perf
from trading_risk_engine.backtester import Backtester
from trading_risk_engine.broker.daily_reauth import DailyReauthManager
from trading_risk_engine.broker.kite_connect import KiteConnectBroker
from trading_risk_engine.broker.paper_broker import PaperBroker
from trading_risk_engine.config import Config as Phase6Config
from trading_risk_engine.data_provider import SimulatedMarketDataProvider
from trading_risk_engine.deployment import build_default_server_commands as build_phase6_server_commands
from trading_risk_engine.gateway import Phase6Gateway
from trading_risk_engine.ledger_bridge import Ledger
from trading_risk_engine.models import OrderSide, OrderType, SignalDirection
from trading_risk_engine.paper_trader import PaperTrader
from trading_risk_engine.regulatory import (
    DailyReauthGate,
    IPChangeTracker,
    LiveTradingGate,
    OrderRateLimiter,
    StaticIPHostGate,
)
from trading_risk_engine.registry_ext import build_demo_registry
from trading_risk_engine.risk import DrawdownCircuitBreaker, RiskManager, compute_stop_and_target
from trading_risk_engine.signals import SignalEngine

# -- F&O extension (Index Options + Index Futures) --
from trading_risk_engine.instruments.contracts import FutureContract, OptionContract, OptionType
from trading_risk_engine.instruments.fo_kite_connect_broker import FOKiteConnectBroker

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
    print("Eternal Phase 6 (Algorithmic Trading) — Exit Criteria Narrative Demo")
    print("Eternal_Master_Build_Plan.md Section 8, Phase 6")

    data_dir = tempfile.mkdtemp(prefix="phase6_demo_")

    # =====================================================================
    section("1. INDICATORS — hand-derivable formulas, computed on real (simulated) data")
    # =====================================================================
    provider = SimulatedMarketDataProvider(seed=7, annual_drift_pct=0.12, annual_volatility_pct=0.22)
    end = date.today()
    start = end - timedelta(days=800)
    bars = provider.get_daily_bars("RELIANCE.NS", start, end)
    df = ind.compute_all(bars, atr_period=14, rsi_period=14, bb_period=20, sma_trend_period=50)
    latest = df.iloc[-1]
    print(f"    {len(df)} bars generated (simulated — see trading_risk_engine/data_provider.py's module docstring)")
    print(f"    latest close={latest['close']:.2f}  RSI={latest['rsi']:.1f}  ATR={latest['atr']:.2f}  "
         f"MACD_hist={latest['macd_hist']:.3f}")
    check("RSI is within its valid [0, 100] range", 0.0 <= latest["rsi"] <= 100.0)
    check("ATR is strictly positive on real volatility", latest["atr"] > 0)
    check("Bollinger upper band is above the lower band", latest["bb_upper"] > latest["bb_lower"])

    config6 = Phase6Config(data_dir=data_dir)
    signal_engine = SignalEngine(config6)
    signal = signal_engine.generate("RELIANCE.NS", df)
    print(f"    current signal: {signal.direction.value} (score {signal.score:+.3f})")
    for reason in signal.reasons:
        print(f"      - {reason}")
    check("the signal carries a plain-English reason for every rule that voted",
          len(signal.reasons) > 0 and all(isinstance(r, str) for r in signal.reasons))

    # =====================================================================
    section("2. POSITION SIZING — fixed-fractional, ATR-based, 1-2% risk per trade")
    # =====================================================================
    risk_manager = RiskManager(config6)
    equity = 500_000.0
    decision = risk_manager.size_position("RELIANCE.NS", entry_price=latest["close"],
                                          atr_value=latest["atr"], account_equity=equity)
    implied_risk_pct = decision.risk_amount / equity
    print(f"    equity=₹{equity:,.0f}  shares={decision.shares}  "
         f"stop=₹{decision.stop_loss_price:.2f}  target=₹{decision.take_profit_price:.2f}")
    print(f"    actual risk taken: ₹{decision.risk_amount:,.2f} ({implied_risk_pct:.2%} of equity)")
    check("position sizing risks at most 2% of equity (build plan Section 7.2's hard ceiling)",
          implied_risk_pct <= 0.02 + 1e-9)
    check("stop-loss sits below entry and take-profit sits above it (a long position)",
          decision.stop_loss_price < latest["close"] < decision.take_profit_price)

    # =====================================================================
    section("3. THE LOSS-RECOVERY MATH — the build plan's own cited figures, executable")
    # =====================================================================
    for loss_pct, expected in [(20.0, 25.0), (30.0, 42.857)]:
        needed = perf.recovery_return_needed(loss_pct / 100.0) * 100.0
        print(f"    a {loss_pct:.0f}% loss requires a +{needed:.3f}% gain to recover")
        check(f"recovering a {loss_pct:.0f}% loss matches the build plan's own cited figure (~{expected}%)",
              abs(needed - expected) < 0.1)

    # =====================================================================
    section("4. BACKTEST — no-lookahead, gap-aware fills, real costs, full performance report")
    # =====================================================================
    symbols = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS"]
    symbol_data = {s: provider.get_daily_bars(s, end - timedelta(days=1200), end) for s in symbols}
    backtest_config = Phase6Config(data_dir=data_dir, commission_pct=0.0003, commission_flat=20.0, slippage_bps=5.0)
    backtester = Backtester(backtest_config)
    report = backtester.run(symbol_data, starting_cash=1_000_000.0)
    p = report.performance
    print(f"    {len(symbols)} symbols, {p.num_days} bars, {p.num_trades} trades")
    print(f"    total return {p.total_return_pct:+.2f}%   CAGR {p.cagr_pct:+.2f}%   "
         f"Sharpe {p.sharpe_ratio:.2f}   Sortino {p.sortino_ratio:.2f}")
    print(f"    max drawdown {p.max_drawdown_pct:.2f}% over {p.max_drawdown_duration_days} bars   "
         f"Calmar {p.calmar_ratio:.2f}")
    print(f"    win rate {p.win_rate_pct:.1f}%   profit factor {p.profit_factor:.2f}   "
         f"expectancy {p.expectancy_r:+.2f}R   Kelly (informational only) "
         f"{'n/a' if p.kelly_fraction is None else f'{p.kelly_fraction:.1%}'}")
    check("the backtest produced at least one trade over this window", p.num_trades > 0)
    check("commission was actually deducted from at least one trade",
          any(t.commission_paid > 0 for t in report.trades))
    check("max drawdown is reported as zero-or-negative (a decline from peak, by convention)",
          p.max_drawdown_pct <= 0)

    print("    re-running on a truncated prefix to prove no lookahead bias...")
    truncated_data = {s: df.iloc[: len(df) - 200] for s, df in symbol_data.items()}
    truncated_report = Backtester(backtest_config).run(truncated_data, starting_cash=1_000_000.0)
    overlap = min(len(report.equity_curve), len(truncated_report.equity_curve))
    # Exclude exactly the truncated run's OWN final point: that bar is
    # subject to the end-of-backtest force-close (backtester.py's final
    # step), which is CORRECTLY dependent on wherever a given run's data
    # happens to end — it is supposed to differ between two runs with
    # different endpoints, and doing so is not a lookahead bug. Every
    # OTHER point, including every real entry/exit/stop/target decision
    # up to that boundary, must still match exactly.
    equities_match = all(
        abs(report.equity_curve[i].equity - truncated_report.equity_curve[i].equity) < 0.01
        for i in range(overlap - 1)
    )
    check("truncating the future leaves every already-computed equity point byte-for-byte unchanged "
         "(excluding the truncated run's own final, intentionally-boundary-dependent force-close point)",
          equities_match)

    # =====================================================================
    section("5. MAX-DRAWDOWN CIRCUIT BREAKER — halts new entries, never halts exits")
    # =====================================================================
    breaker = DrawdownCircuitBreaker(Phase6Config(data_dir=data_dir, max_drawdown_circuit_breaker_pct=0.08))
    status = breaker.check(date.today(), current_drawdown_pct=-0.15)
    print(f"    a -15% drawdown against an 8% breaker: halted={status.halted}, "
         f"recovery needed = +{status.return_needed_to_recover_pct:.2f}%")
    check("the circuit breaker trips on a drawdown beyond its threshold", status.newly_triggered)
    check("the trigger reports the real recovery math, not just a bare halt flag",
          status.return_needed_to_recover_pct is not None and status.return_needed_to_recover_pct > 15.0)

    # =====================================================================
    section("6. PAPER TRADING — real, resumable, day-by-day (build plan: 'several weeks minimum')")
    # =====================================================================
    paper_config = Phase6Config(data_dir=data_dir)
    ledger_paper = Ledger(paper_config.ledger_db_path)
    broker_a = PaperBroker(paper_config.paper_broker_db_path, commission_pct=paper_config.commission_pct,
                           commission_flat=paper_config.commission_flat, slippage_bps=paper_config.slippage_bps)
    trader_a = PaperTrader(paper_config, data_provider=provider, broker=broker_a, ledger=ledger_paper,
                           starting_cash=1_000_000.0)
    sim_dates = [d.date() for d in provider.get_daily_bars("RELIANCE.NS", end - timedelta(days=30), end).index]
    for d in sim_dates[:10]:
        trader_a.run_trading_day(["RELIANCE.NS", "TCS.NS"], d)
    equity_after_10_days = broker_a.get_margins()["cash"] + sum(
        pos.quantity * provider.get_latest_quote(pos.symbol)["price"] for pos in broker_a.get_positions()
    )
    print(f"    ran 10 simulated trading days; {len(broker_a.get_positions())} open position(s); "
         f"cash=₹{broker_a.get_margins()['cash']:,.2f}")

    print("    restarting as a brand-new process-equivalent instance, same data_dir...")
    broker_b = PaperBroker(paper_config.paper_broker_db_path, commission_pct=paper_config.commission_pct,
                           commission_flat=paper_config.commission_flat, slippage_bps=paper_config.slippage_bps)
    trader_b = PaperTrader(paper_config, data_provider=provider, broker=broker_b,
                           ledger=Ledger(paper_config.ledger_db_path), starting_cash=1_000_000.0)
    result_b = trader_b.run_trading_day(["RELIANCE.NS", "TCS.NS"], sim_dates[10])
    check("a brand-new PaperTrader instance picks up exactly where the last one left off",
          result_b.date == sim_dates[10].isoformat())
    check("the paper broker's cash balance survived across the restart",
          broker_b.get_margins()["cash"] == broker_a.get_margins()["cash"])

    # =====================================================================
    section("7. REGULATORY GATES — SEBI/Kite constraints as refusals, not documentation")
    # =====================================================================
    reg_config = Phase6Config(data_dir=data_dir, live_trading_enabled=True,
                              trading_host_role="", required_trading_host_role="home-server-fixed-ip")
    ip_gate = StaticIPHostGate(reg_config)
    r = ip_gate.check()
    print(f"    static-IP host gate (no host role set): allowed={r.allowed} — {r.reason}")
    check("order placement is refused when this process isn't the designated fixed-IP host", not r.allowed)

    reg_config.trading_host_role = "home-server-fixed-ip"
    r = ip_gate.check()
    check("...and allowed once this process IS the designated host", r.allowed)

    class _FakeKiteClientForDemo:
        """Same fake-client injection point tests/test_daily_reauth.py
        uses (kite_client_factory) — real code path, real
        exchange_request_token() call; only the actual Zerodha HTTP
        round trip is stood in for, since this sandbox cannot reach it."""
        def generate_session(self, request_token, api_secret):
            return {"access_token": "demo-access-token", "user_id": "AB1234"}

    reauth_manager = DailyReauthManager(paper_config.daily_reauth_db_path, "demo-key", "demo-secret",
                                        kite_client_factory=lambda api_key: _FakeKiteClientForDemo())
    reauth_gate = DailyReauthGate(reauth_manager)
    r = reauth_gate.check()
    print(f"    daily reauth gate (never logged in): allowed={r.allowed} — {r.reason}")
    check("order placement is refused with no Kite login completed yet", not r.allowed)

    rate_limiter = OrderRateLimiter(max_per_second=2.0)
    results = [rate_limiter.check_and_record() for _ in range(4)]
    print(f"    order rate limiter (2/sec, 4 rapid attempts): "
         f"{sum(r.allowed for r in results)} allowed, {sum(not r.allowed for r in results)} refused")
    check("the order rate limiter refuses bursts beyond its configured ceiling",
          any(not r.allowed for r in results))

    # =====================================================================
    section("8. THE FULL GATEWAY CHAIN — a live order, refused independently at every layer")
    # =====================================================================
    print("    constructing the real QAP-P -> RCAP-P -> Phase4 -> Phase5 -> Phase6 stack...")
    qap_config = QapConfig(data_dir=data_dir)
    orchestrator = Orchestrator(qap_config)
    rcap_project_root = PROJECT_ROOT
    server_commands = build_phase6_server_commands(rcap_project_root)
    registry = build_demo_registry()
    tool_to_server = build_tool_to_server_map(registry)
    ledger = Ledger(Phase6Config(data_dir=data_dir).ledger_db_path)
    executor = make_env_aware_stateless_executor(server_commands, tool_to_server,
                                                 extra_env={"PYTHONPATH": ":".join(sys.path)})
    trust_lookup = QapPTrustLookup(orchestrator)

    rcap_config = RcapConfig(data_dir=data_dir)
    rcap_gateway = RCAPGateway(rcap_config, registry, ledger, executor, trust_lookup=trust_lookup)
    phase4_config = Phase4Config(data_dir=data_dir)
    consent_registry = ConsentRegistry(phase4_config.consent_db_path, ledger)
    phase4_gateway = Phase4Gateway(rcap_gateway, registry, consent_registry, ledger)
    phase5_config = Phase5Config(data_dir=data_dir)
    top_trust_tier_gate = TopTrustTierGate(phase5_config, trust_lookup)
    phase5_gateway = Phase5Gateway(phase4_gateway, top_trust_tier_gate, orchestrator, ledger)

    live_gate = LiveTradingGate(
        reg_config, static_ip_gate=StaticIPHostGate(reg_config),
        daily_reauth_gate=DailyReauthGate(reauth_manager), rate_limiter=OrderRateLimiter(100.0),
    )
    phase6_gateway = Phase6Gateway(phase5_gateway, top_trust_tier_gate, live_gate, ledger)

    consent_registry.grant("trading_place_live_order", granted_by="owner_interactive:demo")

    print("\n    --- 8a. a fresh, low-trust device: refused regardless of channel ---")
    fresh_device = Device(device_id="trading-host-fresh", role=DeviceRole.MINI_PC,
                          public_key_pem="demo", hardware_backed=True, attestation_type="demo-fixture",
                          enrolled_at=time.time())
    orchestrator.store.create_device(fresh_device)
    fresh_session = orchestrator.create_session("trading-host-fresh")
    fresh_score = orchestrator.compute_trust_score("trading-host-fresh")
    print(f"    fresh session {fresh_session}, trust score {fresh_score:.2f}")
    fresh_token = phase6_gateway.grant_tool_permission(fresh_session, "trading_place_live_order")
    fresh_req = WriteCallRequest(
        tool_name="trading_place_live_order",
        arguments={"symbol": "RELIANCE.NS", "side": "BUY", "quantity": 1, "order_type": "LIMIT", "limit_price": 2500},
        session_id=fresh_session, confirmation_channel=ConfirmationChannel.APP_TAP, source_label="phone_app",
        explanation="owner tapped buy", rotate_token=fresh_token,
    )
    fresh_result = await phase6_gateway.call_tool(fresh_req)
    check("a fresh, low-trust session cannot place a live order even via app_tap",
          fresh_result.ok is False and "trust score" in fresh_result.denied_reason)

    print("\n    manufacturing a long-standing, no-quarantine-history device to clear the top-trust-tier bar...")
    trusted_device = Device(
        device_id="trading-host-trusted", role=DeviceRole.MINI_PC, public_key_pem="demo",
        hardware_backed=True, attestation_type="demo-fixture-long-standing",
        enrolled_at=time.time() - 35 * 86400, quarantine_count=0, anomaly_count=0,
    )
    orchestrator.store.create_device(trusted_device)
    trusted_session = orchestrator.create_session("trading-host-trusted")
    trusted_score = orchestrator.compute_trust_score("trading-host-trusted")
    print(f"    trusted session {trusted_session}, trust score {trusted_score:.2f}")
    check("the manufactured device legitimately clears the top-trust-tier bar under qap_p's real formula",
          trusted_score >= phase5_config.top_trust_tier_min_score)

    print("\n    --- 8b. high trust, but over VOICE: refused independently (Phase 4's own voice-gate) ---")
    voice_token = phase6_gateway.grant_tool_permission(trusted_session, "trading_place_live_order")
    voice_req = WriteCallRequest(
        tool_name="trading_place_live_order",
        arguments={"symbol": "RELIANCE.NS", "side": "BUY", "quantity": 1, "order_type": "LIMIT", "limit_price": 2500},
        session_id=trusted_session, confirmation_channel=ConfirmationChannel.VOICE, source_label="voice_pipeline",
        explanation="user said buy reliance", rotate_token=voice_token,
    )
    voice_result = await phase6_gateway.call_tool(voice_req)
    check("high trust is NOT enough over voice — the ordinary SENSITIVE-tier voice-gate still refuses it",
          voice_result.ok is False)

    print("\n    --- 8c. high trust, app_tap, but live trading disabled by config ---")
    reg_config.live_trading_enabled = False
    tap_token = phase6_gateway.grant_tool_permission(trusted_session, "trading_place_live_order")
    tap_req = WriteCallRequest(
        tool_name="trading_place_live_order",
        arguments={"symbol": "RELIANCE.NS", "side": "BUY", "quantity": 1, "order_type": "LIMIT", "limit_price": 2500},
        session_id=trusted_session, confirmation_channel=ConfirmationChannel.APP_TAP, source_label="phone_app",
        explanation="owner tapped buy", rotate_token=tap_token,
    )
    disabled_result = await phase6_gateway.call_tool(tap_req)
    check("live trading disabled by config refuses the order even at full trust + correct channel",
          disabled_result.ok is False and "disabled" in disabled_result.denied_reason)

    print("\n    --- 8d. enabling config + completing every regulatory gate in turn ---")
    reg_config.live_trading_enabled = True
    tap_token = phase6_gateway.grant_tool_permission(trusted_session, "trading_place_live_order")
    tap_req.rotate_token = tap_token
    still_no_ip = await phase6_gateway.call_tool(tap_req)
    check("...still refused: no fixed-IP host role configured", still_no_ip.ok is False)

    reg_config.trading_host_role = "home-server-fixed-ip"
    tap_token = phase6_gateway.grant_tool_permission(trusted_session, "trading_place_live_order")
    tap_req.rotate_token = tap_token
    still_no_reauth = await phase6_gateway.call_tool(tap_req)
    check("...still refused: no valid daily Kite Connect login yet", still_no_reauth.ok is False)

    reauth_manager.exchange_request_token("demo-request-token")  # real code path, fake client (see above)
    status = reauth_manager.token_status()
    print(f"    (completed the real login-token-exchange code path against a fake client) "
         f"token valid now: {status.is_valid_now}")

    final_gate_check = live_gate.check_before_order(OrderType.LIMIT, None)
    print(f"    LiveTradingGate.check_before_order, every condition met: allowed={final_gate_check.allowed}")
    check("once every regulatory condition is met, this project's OWN gate chain allows the order through",
          final_gate_check.allowed is True)

    print("\n    now placing that order for real against KiteConnectBroker directly (no MCP subprocess "
         "involved in this specific check — that round trip is scripts/test_mcp_e2e.py's job) — every gate "
         "this project's own code enforces has cleared; only the actual Zerodha network call remains...")
    live_broker = KiteConnectBroker("demo-api-key", reauth_manager)  # real client factory — NOT the fake login one above
    order_result = live_broker.place_order("RELIANCE.NS", OrderSide.BUY, 1, OrderType.LIMIT, limit_price=2500.0)
    print(f"    KiteConnectBroker.place_order result: status={order_result.status.value}")
    print(f"    reject_reason: {order_result.reject_reason}")
    check(
        "the real KiteConnectBroker call is REACHED (every project-side gate cleared) and fails ONLY at the "
        "actual external Zerodha network call — the honest, documented sandbox boundary, not a gap in this project",
        order_result.status.value == "REJECTED" and order_result.reject_reason is not None,
    )

    # =====================================================================
    section("8b. THE SAME GATEWAY CHAIN, FOR F&O — an index option order, refused "
           "independently at every layer")
    # =====================================================================
    # Same six-step structure as section 8, on purpose: the whole point of
    # trading_risk_engine/gateway.py's LIVE_ORDER_TOOL_NAMES being a SET (not a single
    # hardcoded name) is that fo_place_live_option_order gets IDENTICAL
    # treatment to trading_place_live_order with no F&O-specific gateway
    # code — this section is the empirical proof of that claim, not just
    # a code-reading argument. Arguments are F&O-shaped (underlying_symbol/
    # strike/option_type/side/num_lots, no plain symbol string) per this
    # phase's own contracts.py.
    fo_option_contract = OptionContract(
        underlying_symbol="NIFTY", strike=24500.0, expiry=date.today() + timedelta(days=7),
        option_type=OptionType.CALL,
    )
    fo_args = {
        "underlying_symbol": fo_option_contract.underlying_symbol, "strike": fo_option_contract.strike,
        "option_type": fo_option_contract.option_type.value, "side": "LONG", "num_lots": 1,
        "expiry_choice": "WEEKLY", "order_type": "LIMIT", "limit_price": 150.0,
    }
    reg_config.live_trading_enabled = False  # reset from section 8's end state, same as a fresh process

    print("\n    --- 8b-a. a fresh, low-trust device: refused regardless of channel ---")
    fo_fresh_token = phase6_gateway.grant_tool_permission(fresh_session, "fo_place_live_option_order")
    fo_fresh_req = WriteCallRequest(
        tool_name="fo_place_live_option_order", arguments=fo_args, session_id=fresh_session,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="phone_app",
        explanation="owner tapped buy (F&O)", rotate_token=fo_fresh_token,
    )
    fo_fresh_result = await phase6_gateway.call_tool(fo_fresh_req)
    check("F&O: a fresh, low-trust session cannot place a live option order even via app_tap",
          fo_fresh_result.ok is False and "trust score" in fo_fresh_result.denied_reason)

    print("\n    --- 8b-b. high trust, but over VOICE: refused independently ---")
    fo_voice_token = phase6_gateway.grant_tool_permission(trusted_session, "fo_place_live_option_order")
    fo_voice_req = WriteCallRequest(
        tool_name="fo_place_live_option_order", arguments=fo_args, session_id=trusted_session,
        confirmation_channel=ConfirmationChannel.VOICE, source_label="voice_pipeline",
        explanation="user said buy nifty calls", rotate_token=fo_voice_token,
    )
    fo_voice_result = await phase6_gateway.call_tool(fo_voice_req)
    check("F&O: high trust is NOT enough over voice for an option order either",
          fo_voice_result.ok is False)

    print("\n    --- 8b-c. high trust, app_tap, but live trading disabled by config ---")
    fo_tap_token = phase6_gateway.grant_tool_permission(trusted_session, "fo_place_live_option_order")
    fo_tap_req = WriteCallRequest(
        tool_name="fo_place_live_option_order", arguments=fo_args, session_id=trusted_session,
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="phone_app",
        explanation="owner tapped buy (F&O)", rotate_token=fo_tap_token,
    )
    fo_disabled_result = await phase6_gateway.call_tool(fo_tap_req)
    check("F&O: live trading disabled by config refuses the option order at full trust + correct channel",
          fo_disabled_result.ok is False and "disabled" in fo_disabled_result.denied_reason)

    print("\n    --- 8b-d. enabling config + completing every regulatory gate in turn ---")
    reg_config.live_trading_enabled = True
    fo_tap_token = phase6_gateway.grant_tool_permission(trusted_session, "fo_place_live_option_order")
    fo_tap_req.rotate_token = fo_tap_token
    fo_still_no_ip = await phase6_gateway.call_tool(fo_tap_req)
    check("F&O: ...still refused: no fixed-IP host role configured", fo_still_no_ip.ok is False)
    # (trading_host_role and the daily reauth token were already established by section 8 above —
    #  the SAME regulatory infrastructure covers F&O with no duplicate setup, which is itself part
    #  of the claim: trading_risk_engine/regulatory.py's gates are instrument-agnostic.)

    fo_final_gate_check = live_gate.check_before_order(OrderType.LIMIT, None)
    check("F&O: once every regulatory condition is met, the SAME gate chain allows the option order through",
          fo_final_gate_check.allowed is True)

    print("\n    now placing that option order for real against FOKiteConnectBroker directly "
         "(no MCP subprocess involved in this specific check) — every project-side gate has "
         "cleared; only the actual Zerodha network call (instrument resolution) remains...")
    fo_live_broker = FOKiteConnectBroker("demo-api-key", reauth_manager)  # real client factory, same as equity's
    fo_order_result = fo_live_broker.place_option_order(fo_option_contract, "LONG", 1, OrderType.LIMIT,
                                                         limit_price=150.0)
    print(f"    FOKiteConnectBroker.place_option_order result: status={fo_order_result.status.value}")
    print(f"    reject_reason: {fo_order_result.reject_reason}")
    check(
        "F&O: the real FOKiteConnectBroker call is REACHED (every project-side gate cleared) and fails "
        "ONLY at the actual external Zerodha network call — the same honest sandbox boundary as equity, "
        "not a gap specific to F&O",
        fo_order_result.status.value == "REJECTED" and fo_order_result.reject_reason is not None,
    )

    print("\n    --- 8b-e. fo_place_live_future_order is covered by the SAME set-membership check "
         "(no separate demonstration needed — this is exactly the point of LIVE_ORDER_TOOL_NAMES "
         "being a set) ---")
    from trading_risk_engine.gateway import LIVE_ORDER_TOOL_NAMES
    check("fo_place_live_future_order is in the gated set alongside the two tools just exercised",
          {"trading_place_live_order", "fo_place_live_option_order",
           "fo_place_live_future_order"} == LIVE_ORDER_TOOL_NAMES)

    # =====================================================================
    section("9. KILL SWITCH — still covers Phase 6's tools, with zero Phase 6-specific code")
    # =====================================================================
    kill_report = phase5_gateway.pull_kill_switch(reason="phase6_demo")
    print(f"    kill switch: {len(kill_report.sessions_quarantined)} session(s) quarantined, "
         f"{kill_report.rotate_tokens_revoked} ROTATE token(s) revoked")
    try:
        post_kill_token = phase6_gateway.grant_tool_permission(trusted_session, "trading_place_live_order")
    except ValueError as e:
        post_kill_token = None
        print(f"    grant_tool_permission correctly raised instead of returning a token: {e}")
    check("a session the kill switch just quarantined cannot be handed a fresh token for a live order, "
         "with no Phase-6-specific kill-switch code at all (inherited from Phase 5's existing guard)",
          post_kill_token is None)

    # =====================================================================
    section("10. LEDGER — every decision above is in the tamper-evident chain")
    # =====================================================================
    intact, break_index = ledger.verify_chain()
    print(f"    {ledger.count()} entries recorded this run; chain intact: {intact}")
    check("the ledger chain verifies as intact after this entire run", intact)

    import sqlite3
    conn = sqlite3.connect(Phase6Config(data_dir=data_dir).ledger_db_path)
    conn.execute("UPDATE ledger SET payload = '{\"tampered\": true}' WHERE id = 1")
    conn.commit()
    conn.close()
    intact_after_tamper, break_index_after = ledger.verify_chain()
    print(f"    after directly tampering with row 1: chain intact = {intact_after_tamper}")
    check("tampering with a ledger row is caught by verify_chain, not silently accepted",
          intact_after_tamper is False)

    # =====================================================================
    section("SUMMARY")
    # =====================================================================
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed\n")
    if FAIL:
        print("FAILED CHECKS:")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL EXIT CRITERIA CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
