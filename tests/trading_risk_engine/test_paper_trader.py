import datetime as dt
import shutil

import pandas as pd
import pytest

from trading_risk_engine.broker.paper_broker import PaperBroker
from trading_risk_engine.config import Config
from trading_risk_engine.data_provider import MarketDataProvider
from trading_risk_engine.ledger_bridge import Ledger
from trading_risk_engine.paper_trader import PaperTrader
from trading_risk_engine.risk import RiskManager
from trading_risk_engine.signals import SignalEngine, SignalRule


class _FixedDataProvider(MarketDataProvider):
    """Returns a hand-built, fixed OHLCV frame regardless of the
    requested start date (clipped only at the upper end) — the paper-
    trading analogue of the backtester tests' fully-controlled scenario
    construction, needed for the same reason: exact, hand-verifiable
    prices and fills rather than a stochastic series."""

    def __init__(self, df: pd.DataFrame):
        self.df = df

    def get_daily_bars(self, symbol, start, end):
        return self.df.loc[: pd.Timestamp(end)]

    def get_latest_quote(self, symbol):
        last = self.df.iloc[-1]
        return {"symbol": symbol, "price": float(last["close"]), "as_of": self.df.index[-1].isoformat()}


def _cfg(tmp_dir: str, **overrides) -> Config:
    defaults = dict(
        atr_period=1, atr_stop_multiplier=2.0, atr_target_multiplier=3.0,
        risk_per_trade_pct=0.02, max_position_pct_of_equity=1.0, max_total_exposure_pct_of_equity=1.0,
        commission_pct=0.0, commission_flat=0.0, slippage_bps=0.0,
        max_drawdown_circuit_breaker_pct=0.10, circuit_breaker_period="month",
        data_dir=tmp_dir,
    )
    defaults.update(overrides)
    return Config(**defaults)


def _buy_on_date_rule(buy_date: dt.date, sell_date=None):
    def fn(df, config):
        current = df.index[-1].date()
        if current == buy_date:
            return 1.0, f"test: buy trigger on {buy_date}"
        if sell_date is not None and current == sell_date:
            return -1.0, f"test: sell trigger on {sell_date}"
        return 0.0, None
    return SignalRule("test_trigger", 1.0, fn)


def _rows_df():
    dates = pd.bdate_range("2026-03-02", periods=10)
    rows = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},   # 03-02
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},   # 03-03
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},   # 03-04 <- BUY decided
        {"open": 110.0, "high": 112.0, "low": 108.0, "close": 109.0},  # 03-05 <- fill day (open=110)
        {"open": 105.0, "high": 106.0, "low": 103.0, "close": 104.0},  # 03-06 <- stop-loss gap-through day
        {"open": 105.0, "high": 106.0, "low": 104.0, "close": 105.0},  # 03-09
        {"open": 105.0, "high": 106.0, "low": 104.0, "close": 105.0},  # 03-10
        {"open": 105.0, "high": 106.0, "low": 104.0, "close": 105.0},  # 03-11
        {"open": 105.0, "high": 106.0, "low": 104.0, "close": 105.0},  # 03-12
        {"open": 105.0, "high": 106.0, "low": 104.0, "close": 105.0},  # 03-13
    ]
    df = pd.DataFrame(rows, index=dates)
    df.index.name = "date"
    df["volume"] = 100_000.0
    return df


# ---------------------------------------------------------------------------
# Full decide-yesterday / fill-today walk, matching the equivalent
# backtester scenario exactly (see test_backtester.py's stop-loss test) —
# a strong cross-system consistency check, not just an isolated unit test.
# ---------------------------------------------------------------------------

def test_paper_trader_decide_fill_stop_matches_backtester_equivalent(tmp_path):
    df = _rows_df()
    provider = _FixedDataProvider(df)
    config = _cfg(str(tmp_path))
    engine = SignalEngine(config, rules=[_buy_on_date_rule(dt.date(2026, 3, 4))])
    trader = PaperTrader(config, data_provider=provider, signal_engine=engine, starting_cash=100_000.0)

    dates = [d.date() for d in df.index[:5]]  # through 2026-03-06
    results = [trader.run_trading_day(["TEST.NS"], d) for d in dates]

    # Day 0,1: nothing happens yet.
    assert results[0].fills == [] and results[0].equity == pytest.approx(100_000.0)
    assert results[1].fills == [] and results[1].equity == pytest.approx(100_000.0)

    # Day 2 (2026-03-04): BUY decided, nothing fills yet today.
    assert results[2].fills == []
    assert len(results[2].new_pending_decisions) == 1
    assert results[2].new_pending_decisions[0]["action"] == "BUY"
    assert results[2].equity == pytest.approx(100_000.0)

    # Day 3 (2026-03-05): entry fills at today's OPEN (110), re-anchored
    # stop/target -> stop=106, target=116; equity marks to today's close (109).
    assert len(results[3].fills) == 1
    assert results[3].fills[0]["action"] == "BUY"
    assert results[3].fills[0]["order"]["fill_price"] == pytest.approx(110.0)
    assert results[3].fills[0]["order"]["quantity"] == 500
    assert results[3].cash == pytest.approx(45_000.0)
    assert results[3].equity == pytest.approx(45_000.0 + 500 * 109.0)  # 99,500

    # Day 4 (2026-03-06): gapped-through stop-loss -> exit at min(106, open=105) = 105.
    assert len(results[4].fills) == 1
    assert results[4].fills[0]["action"] == "STOP_OR_TARGET"
    assert results[4].fills[0]["order"]["fill_price"] == pytest.approx(105.0)
    assert results[4].cash == pytest.approx(97_500.0)
    assert results[4].equity == pytest.approx(97_500.0)
    assert results[4].positions_value == pytest.approx(0.0)


def test_paper_trader_rejects_out_of_order_or_repeated_dates(tmp_path):
    df = _rows_df()
    provider = _FixedDataProvider(df)
    config = _cfg(str(tmp_path))
    trader = PaperTrader(config, data_provider=provider, starting_cash=100_000.0)

    d0 = df.index[0].date()
    d1 = df.index[1].date()
    trader.run_trading_day(["TEST.NS"], d0)
    trader.run_trading_day(["TEST.NS"], d1)
    with pytest.raises(ValueError):
        trader.run_trading_day(["TEST.NS"], d1)  # repeat
    with pytest.raises(ValueError):
        trader.run_trading_day(["TEST.NS"], d0)  # out of order


# ---------------------------------------------------------------------------
# Cross-process resumability — a NEW PaperTrader instance, pointed at the
# same data_dir, must pick up exactly where the first one left off.
# ---------------------------------------------------------------------------

def test_paper_trader_state_persists_across_instances(tmp_path):
    df = _rows_df()
    provider = _FixedDataProvider(df)
    config = _cfg(str(tmp_path))
    engine_rule = _buy_on_date_rule(dt.date(2026, 3, 4))

    trader_a = PaperTrader(config, data_provider=provider,
                           signal_engine=SignalEngine(config, rules=[engine_rule]), starting_cash=100_000.0)
    for d in [x.date() for x in df.index[:3]]:  # through 2026-03-04 -> BUY decided, not yet filled
        trader_a.run_trading_day(["TEST.NS"], d)

    # A brand-new process/instance: fresh PaperTrader, fresh SignalEngine,
    # fresh PaperBroker object — but pointed at the SAME config.data_dir
    # (hence the same paper_broker.db and paper_trader_state.db files).
    config_b = _cfg(str(tmp_path))  # re-derives the same db paths from the same data_dir
    broker_b = PaperBroker(  # reopen the SAME sqlite file, not a fresh one — same cost config as trader_a's default
        config_b.paper_broker_db_path, commission_pct=config_b.commission_pct,
        commission_flat=config_b.commission_flat, slippage_bps=config_b.slippage_bps,
    )
    trader_b = PaperTrader(config_b, data_provider=provider,
                           signal_engine=SignalEngine(config_b, rules=[engine_rule]),
                           broker=broker_b, ledger=Ledger(config_b.ledger_db_path))

    result = trader_b.run_trading_day(["TEST.NS"], df.index[3].date())  # 2026-03-05 -> should fill the pending BUY
    assert len(result.fills) == 1
    assert result.fills[0]["action"] == "BUY"
    assert result.fills[0]["order"]["fill_price"] == pytest.approx(110.0)


# ---------------------------------------------------------------------------
# Circuit breaker integration
# ---------------------------------------------------------------------------

def test_paper_trader_circuit_breaker_blocks_new_buy_after_large_loss(tmp_path):
    dates = pd.bdate_range("2026-03-02", periods=8)
    rows = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},   # BUY #1 decided
        {"open": 110.0, "high": 112.0, "low": 108.0, "close": 109.0},  # fill @110
        {"open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0},      # catastrophic gap -> stop-loss, huge loss
        {"open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0},      # BUY #2 decided (attempted)
        {"open": 51.0, "high": 52.0, "low": 50.0, "close": 51.0},      # would-be fill day for BUY #2
        {"open": 51.0, "high": 52.0, "low": 50.0, "close": 51.0},
    ]
    df = pd.DataFrame(rows, index=dates)
    df.index.name = "date"
    df["volume"] = 100_000.0
    provider = _FixedDataProvider(df)

    config = _cfg(str(tmp_path), max_drawdown_circuit_breaker_pct=0.05, circuit_breaker_period="month")
    rule = _buy_on_date_rule(dt.date(2026, 3, 4))

    # A second rule that also fires BUY on the day after the crash, to
    # attempt (and expect to be refused) a second entry.
    def two_shot_fn(d, cfg):
        current = d.index[-1].date()
        if current in (dt.date(2026, 3, 4), dt.date(2026, 3, 9)):
            return 1.0, "buy trigger"
        return 0.0, None

    engine = SignalEngine(config, rules=[SignalRule("two_shot", 1.0, two_shot_fn)])
    trader = PaperTrader(config, data_provider=provider, signal_engine=engine, starting_cash=100_000.0)

    all_results = [trader.run_trading_day(["TEST.NS"], d.date()) for d in dates[:7]]

    triggered = [r for r in all_results if r.circuit_breaker["newly_triggered"]]
    assert len(triggered) == 1

    # After the circuit breaker trips, the second BUY decision (2026-03-09)
    # must never be queued as a pending action.
    day_after_crash = all_results[5]  # 2026-03-09's own result
    assert day_after_crash.new_pending_decisions == []
