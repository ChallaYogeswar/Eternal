from typing import Optional

import pandas as pd
import pytest

from trading_risk_engine.backtester import Backtester
from trading_risk_engine.config import Config
from trading_risk_engine.models import ExitReason, SignalDirection
from trading_risk_engine.signals import SignalEngine, SignalRule


def _cfg(**overrides) -> Config:
    defaults = dict(
        atr_period=1, atr_stop_multiplier=2.0, atr_target_multiplier=3.0,
        risk_per_trade_pct=0.02, max_position_pct_of_equity=1.0, max_total_exposure_pct_of_equity=1.0,
        commission_pct=0.0, commission_flat=0.0, slippage_bps=0.0,
        max_drawdown_circuit_breaker_pct=0.10, circuit_breaker_period="month",
        data_dir="/tmp/phase6_backtester_test_data",
    )
    defaults.update(overrides)
    return Config(**defaults)


def _bars(rows: list, start="2026-01-01") -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=len(rows))
    df = pd.DataFrame(rows, index=dates)
    df.index.name = "date"
    return df


def _buy_at_len_rule(buy_len: int, sell_len: Optional[int] = None):
    """A fully deterministic 'signal' with no dependence on price levels
    at all — lets a test control exactly which decision bar fires BUY or
    SELL, so the surrounding OHLC can be constructed purely to exercise
    fill/stop/target/commission arithmetic, independent of testing
    signals.py's own rule content (already covered in test_signals.py)."""
    def fn(df, config):
        if len(df) == buy_len:
            return 1.0, f"test: buy trigger at length {buy_len}"
        if sell_len is not None and len(df) == sell_len:
            return -1.0, f"test: sell trigger at length {sell_len}"
        return 0.0, None
    return SignalRule("test_trigger", 1.0, fn)


# ---------------------------------------------------------------------------
# Scenario 1 — stop-loss exit, gapped through, zero costs
# ---------------------------------------------------------------------------
# ATR(period=1) at every bar below is a constant 2.0 (True Range is 2.0 on
# every bar by construction: H-L=2, and each day opens/closes flat at 100
# through bar2, so |H-prevClose| and |L-prevClose| never exceed 2 either).
#
# BUY fires using data through bar index 2 (3 bars) -> fill at bar3's OPEN=110.
#   risk_amount = 100,000 * 0.02 = 2,000
#   stop_distance = ATR(2.0) * atr_stop_multiplier(2.0) = 4.0
#   shares = floor(2000 / 4) = 500
#   stop_loss = 110 - 4 = 106.0 ; take_profit = 110 + 2.0*3.0 = 116.0
#   cash after entry = 100,000 - 500*110 = 45,000
#   equity at bar3 close (109) = 45,000 + 500*109 = 99,500
#
# bar4 = O=105 (gapped BELOW the 106 stop), H=106, L=103, C=104
#   -> stop-loss triggers; open(105) <= stop(106), so fill = min(106,105) = 105 (gap fill, worse than nominal stop)
#   pnl = (105-110)*500 - 0 = -2,500 ; cash after exit = 45,000 + 500*105 = 97,500

def test_backtest_stop_loss_exit_with_gap_hand_calculation():
    rows = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},  # decision bar (len==3) -> BUY
        {"open": 110.0, "high": 112.0, "low": 108.0, "close": 109.0},  # entry fill bar
        {"open": 105.0, "high": 106.0, "low": 103.0, "close": 104.0},  # stop-loss gap-through bar
    ]
    df = _bars(rows)
    df["volume"] = 100_000.0

    engine = SignalEngine(_cfg(), rules=[_buy_at_len_rule(buy_len=3)])
    bt = Backtester(_cfg(), signal_engine=engine)
    report = bt.run({"TEST.NS": df}, starting_cash=100_000.0)

    assert len(report.trades) == 1
    trade = report.trades[0]
    assert trade.exit_reason == ExitReason.STOP_LOSS
    assert trade.quantity == 500
    assert trade.entry_price == pytest.approx(110.0)
    assert trade.exit_price == pytest.approx(105.0)
    assert trade.pnl == pytest.approx(-2500.0)
    assert trade.risk_amount == pytest.approx(2000.0)
    assert trade.r_multiple == pytest.approx(-1.25)  # lost more than 1R because of the gap — an honest property

    equity_values = [p.equity for p in report.equity_curve]
    assert equity_values == pytest.approx([100_000.0, 100_000.0, 100_000.0, 99_500.0, 97_500.0])


# ---------------------------------------------------------------------------
# Scenario 2 — take-profit exit, gapped through, WITH commission + slippage
# ---------------------------------------------------------------------------
# fill_price(entry) = 110 * (1 + 10bps) = 110.11
# shares = floor(2000/4) = 500 (ATR/stop_distance unaffected by slippage)
# entry_commission = 10 + 0.001*110.11*500 = 10 + 55.055 = 65.055
# stop=110.11-4=106.11 ; target=110.11+6=116.11
# bar4: O=117 (gapped ABOVE target 116.11) -> reference=max(116.11,117)=117
#   exit_price = 117*(1-0.001) = 116.883
#   exit_commission = 10 + 0.001*116.883*500 = 10+58.4415 = 68.4415
#   pnl = (116.883-110.11)*500 - (65.055+68.4415) = 3386.5 - 133.4965 = 3253.0035

def test_backtest_take_profit_exit_with_commission_and_slippage_hand_calculation():
    rows = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 110.0, "high": 111.0, "low": 109.0, "close": 110.0},
        {"open": 117.0, "high": 118.0, "low": 116.0, "close": 117.5},
    ]
    df = _bars(rows)
    df["volume"] = 100_000.0

    config = _cfg(commission_pct=0.001, commission_flat=10.0, slippage_bps=10.0)
    engine = SignalEngine(config, rules=[_buy_at_len_rule(buy_len=3)])
    bt = Backtester(config, signal_engine=engine)
    report = bt.run({"TEST.NS": df}, starting_cash=100_000.0)

    assert len(report.trades) == 1
    trade = report.trades[0]
    assert trade.exit_reason == ExitReason.TAKE_PROFIT
    assert trade.entry_price == pytest.approx(110.11, rel=1e-6)
    assert trade.exit_price == pytest.approx(116.883, rel=1e-6)
    assert trade.commission_paid == pytest.approx(65.055 + 68.4415, rel=1e-6)
    assert trade.pnl == pytest.approx(3253.0035, rel=1e-6)

    # Invariant: with exactly one round-trip trade and no other cash
    # flows, ending cash minus starting cash must equal the trade's pnl
    # EXACTLY — a strong, independent cross-check of the whole cash-
    # accounting path (entry cost, exit proceeds, both commissions),
    # not just the pnl formula in isolation.
    ending_cash = report.equity_curve[-1].cash
    assert (ending_cash - 100_000.0) == pytest.approx(trade.pnl, rel=1e-9)


# ---------------------------------------------------------------------------
# Scenario 3 — signal-based exit (no stop/target touched)
# ---------------------------------------------------------------------------

def test_backtest_signal_based_exit():
    rows = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},  # len==3 -> BUY decided
        {"open": 110.0, "high": 112.0, "low": 108.0, "close": 109.0},  # entry fill (stop=106, target=116)
        {"open": 111.0, "high": 112.0, "low": 110.0, "close": 111.0},  # within range, no exit yet
        {"open": 113.0, "high": 114.0, "low": 112.0, "close": 113.0},  # within range -> falls through to signal exit
    ]
    df = _bars(rows)
    df["volume"] = 100_000.0

    engine = SignalEngine(_cfg(), rules=[_buy_at_len_rule(buy_len=3, sell_len=5)])
    bt = Backtester(_cfg(), signal_engine=engine)
    report = bt.run({"TEST.NS": df}, starting_cash=100_000.0)

    assert len(report.trades) == 1
    trade = report.trades[0]
    assert trade.exit_reason == ExitReason.SIGNAL_EXIT
    assert trade.entry_price == pytest.approx(110.0)
    assert trade.exit_price == pytest.approx(113.0)  # bar5's open, zero slippage
    assert trade.pnl == pytest.approx((113.0 - 110.0) * 500)


# ---------------------------------------------------------------------------
# Scenario 4 — position still open at the end of the data window
# ---------------------------------------------------------------------------

def test_backtest_force_closes_open_position_at_end():
    rows = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},  # len==3 -> BUY decided
        {"open": 110.0, "high": 112.0, "low": 108.0, "close": 109.0},  # entry fill; data ends here
    ]
    df = _bars(rows)
    df["volume"] = 100_000.0

    engine = SignalEngine(_cfg(), rules=[_buy_at_len_rule(buy_len=3)])
    bt = Backtester(_cfg(), signal_engine=engine)
    report = bt.run({"TEST.NS": df}, starting_cash=100_000.0)

    assert len(report.trades) == 1
    trade = report.trades[0]
    assert trade.exit_reason == ExitReason.END_OF_BACKTEST
    assert trade.exit_price == pytest.approx(109.0)  # forced closed at the last bar's CLOSE
    assert trade.pnl == pytest.approx((109.0 - 110.0) * 500)
    assert report.equity_curve[-1].positions_value == pytest.approx(0.0)
    assert report.equity_curve[-1].equity == pytest.approx(report.equity_curve[-1].cash)


# ---------------------------------------------------------------------------
# Structural / integration tests
# ---------------------------------------------------------------------------

def test_backtest_requires_aligned_indices():
    df_a = _bars([{"open": 1, "high": 2, "low": 0.5, "close": 1.5}] * 10, start="2026-01-01")
    df_b = _bars([{"open": 1, "high": 2, "low": 0.5, "close": 1.5}] * 10, start="2026-02-01")
    df_a["volume"] = 1000.0
    df_b["volume"] = 1000.0
    bt = Backtester(_cfg())
    with pytest.raises(ValueError, match="identical date index"):
        bt.run({"A": df_a, "B": df_b})


def test_backtest_rejects_empty_input():
    bt = Backtester(_cfg())
    with pytest.raises(ValueError):
        bt.run({})


def test_backtest_respects_max_concurrent_positions():
    # Two symbols, both fire BUY on the same decision bar; cap allows only 1.
    rows = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 110.0, "high": 112.0, "low": 108.0, "close": 109.0},
        {"open": 111.0, "high": 112.0, "low": 110.0, "close": 111.0},
    ]
    df_a = _bars(rows); df_a["volume"] = 100_000.0
    df_b = _bars(rows); df_b["volume"] = 100_000.0

    config = _cfg(max_concurrent_positions=1)
    engine = SignalEngine(config, rules=[_buy_at_len_rule(buy_len=3)])
    bt = Backtester(config, signal_engine=engine)
    report = bt.run({"AAA.NS": df_a, "BBB.NS": df_b}, starting_cash=100_000.0)

    opened_symbols = {t.symbol for t in report.trades}
    assert len(opened_symbols) == 1  # only one of the two was allowed to open


def test_backtest_circuit_breaker_blocks_new_entries_after_large_loss():
    # First trade loses badly enough to breach a tight circuit-breaker
    # threshold; a SECOND buy signal later in the same period must be refused.
    rows = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},  # len==3 -> BUY #1
        {"open": 110.0, "high": 112.0, "low": 108.0, "close": 109.0},  # entry #1 @110, stop=106
        {"open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0},      # catastrophic gap-down -> stop-loss, huge loss
        {"open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0},
        {"open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0},      # len==7 -> BUY #2 attempted
        {"open": 51.0, "high": 52.0, "low": 50.0, "close": 51.0},      # would-be entry #2 bar
    ]
    df = _bars(rows)
    df["volume"] = 100_000.0

    config = _cfg(max_drawdown_circuit_breaker_pct=0.05, circuit_breaker_period="month")

    class _TwoShotRule:
        def fn(self, df, cfg):
            if len(df) in (3, 7):
                return 1.0, "buy trigger"
            return 0.0, None

    rule = SignalRule("two_shot", 1.0, _TwoShotRule().fn)
    engine = SignalEngine(config, rules=[rule])
    bt = Backtester(config, signal_engine=engine)
    report = bt.run({"TEST.NS": df}, starting_cash=100_000.0)

    # Only the first trade should have executed; the second BUY attempt
    # (decision at len==7) falls inside the same halted month.
    assert len(report.trades) == 1
    assert len(report.circuit_breaker_triggers) == 1


# ---------------------------------------------------------------------------
# Regression: numpy scalar leakage into JSON-facing output
# ---------------------------------------------------------------------------

def test_backtest_json_serializable_output_no_numpy_leakage():
    """Regression test for a real bug found via scripts/test_mcp_e2e.py:
    pandas .iloc[] lookups return numpy scalar types, and it is very easy
    for one to silently ride along into a Trade/EquityPoint/
    PerformanceReport field — invisible in-process (numpy floats behave
    like floats for arithmetic and comparisons, and even pass
    pytest.approx) but fatal the moment the result is JSON-serialized for
    a real MCP or HTTP response, where it breaks. Runs a real
    multi-symbol backtest (not hand-picked data, on purpose — this is
    exactly the shape of run that exposed the original bug) and asserts
    the ENTIRE report round-trips through json.dumps."""
    import datetime as _dt
    import json
    from dataclasses import asdict

    from trading_risk_engine.data_provider import SimulatedMarketDataProvider

    provider = SimulatedMarketDataProvider(seed=3)
    symbols = ["AAA.NS", "BBB.NS"]
    symbol_data = {s: provider.get_daily_bars(s, _dt.date(2022, 1, 1), _dt.date(2024, 1, 1)) for s in symbols}
    report = Backtester(_cfg(atr_period=14, sma_trend_period=50)).run(symbol_data, starting_cash=100_000.0)

    payload = {
        "performance": asdict(report.performance),
        "trades": [asdict(t) | {"exit_reason": t.exit_reason.value} for t in report.trades],
        "equity_curve": [asdict(p) for p in report.equity_curve],
    }
    json.dumps(payload)  # must not raise — this is the exact shape an MCP tool response serializes


# ---------------------------------------------------------------------------
# No-lookahead — structural proof at the full-backtest level
# ---------------------------------------------------------------------------

def test_no_lookahead_bias_full_backtest():
    """Running the backtest on a truncated prefix of the data must
    produce IDENTICAL trades and equity curve for every bar that exists
    in both runs — if a later bar's price silently influenced an earlier
    decision, truncating the future would change the past, which is
    exactly the bug this test exists to catch."""
    import numpy as np
    rng = np.random.RandomState(123)
    n = 40
    closes = 100 + np.cumsum(rng.randn(n) * 1.5)
    rows = []
    for i in range(n):
        c = closes[i]
        o = c + rng.uniform(-0.5, 0.5)
        h = max(o, c) + abs(rng.uniform(0.1, 1.0))
        l = min(o, c) - abs(rng.uniform(0.1, 1.0))
        rows.append({"open": o, "high": h, "low": l, "close": c})
    df_full = _bars(rows)
    df_full["volume"] = 100_000.0

    config = _cfg(atr_period=5, sma_trend_period=5, rsi_period=5, bb_period=5,
                  macd_fast_period=3, macd_slow_period=6, macd_signal_period=2,
                  stoch_k_period=5, stoch_d_period=2)
    bt_full = Backtester(config)
    bt_truncated = Backtester(config)

    report_full = bt_full.run({"TEST.NS": df_full}, starting_cash=100_000.0)
    report_truncated = bt_truncated.run({"TEST.NS": df_full.iloc[:30]}, starting_cash=100_000.0)

    overlap = min(len(report_full.equity_curve), len(report_truncated.equity_curve))
    # Exclude exactly the truncated run's own final point: it is subject
    # to the end-of-backtest force-close, which is correctly dependent on
    # wherever THAT run's data happens to end — see
    # demo_exit_criteria.py's equivalent check for the full reasoning,
    # confirmed empirically (not assumed) while building this project by
    # isolating a real divergence down to exactly this one point before
    # concluding it wasn't a lookahead bug.
    full_prefix = [p.equity for p in report_full.equity_curve[: overlap - 1]]
    truncated_prefix = [p.equity for p in report_truncated.equity_curve[: overlap - 1]]
    assert full_prefix == pytest.approx(truncated_prefix)

    # Every trade that happened within the truncated window must also
    # appear, identically, in the full run.
    truncated_trade_keys = {(t.symbol, t.entry_date, t.exit_date, round(t.pnl, 6)) for t in report_truncated.trades
                             if t.exit_reason != ExitReason.END_OF_BACKTEST}
    full_trade_keys = {(t.symbol, t.entry_date, t.exit_date, round(t.pnl, 6)) for t in report_full.trades}
    assert truncated_trade_keys.issubset(full_trade_keys)
