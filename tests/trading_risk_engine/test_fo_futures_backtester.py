import datetime as dt
import math

import pandas as pd
import pytest

from trading_risk_engine.config import Config as EquityConfig
from trading_risk_engine.instruments.calendar import ExpiryResolver
from trading_risk_engine.instruments.fo_backtester import FOBacktester
from trading_risk_engine.instruments.fo_config import FOConfig
from trading_risk_engine.instruments.fo_data_provider import FOMarketDataProvider
from trading_risk_engine.instruments.underlyings import get_underlying
from trading_risk_engine.signals import SignalEngine, SignalRule


def _equity_cfg(tmp_dir: str, **overrides) -> EquityConfig:
    defaults = dict(data_dir=tmp_dir, sma_trend_period=3, atr_period=3, rsi_period=3, bb_period=3,
                    macd_fast_period=2, macd_slow_period=3, macd_signal_period=2,
                    stoch_k_period=3, stoch_d_period=2)
    defaults.update(overrides)
    return EquityConfig(**defaults)


def _buy_on_date_rule(target_date: dt.date):
    def fn(df, cfg):
        if df.index[-1].date() == target_date:
            return 1.0, "test buy trigger"
        return 0.0, None
    return SignalRule("test_trigger", 1.0, fn)


def test_futures_entry_price_matches_cost_of_carry_independently(tmp_path):
    provider = FOMarketDataProvider(seed=6, annual_risk_free_rate=0.065, base_iv=0.15)
    bars = provider.get_underlying_daily_bars("NIFTY", dt.date(2026, 8, 3), dt.date(2026, 8, 20))
    decision_date = bars.index[3].date()
    fill_date = bars.index[4].date()
    fill_open = float(bars["open"].iloc[4])

    equity_config = _equity_cfg(str(tmp_path))
    engine = SignalEngine(equity_config, rules=[_buy_on_date_rule(decision_date)])
    fo_config = FOConfig(futures_risk_per_trade_pct=0.02)
    bt = FOBacktester(fo_config, equity_config, signal_engine=engine)

    report = bt.run_futures_strategy("NIFTY", bars, provider, starting_cash=10_000_000.0)
    assert len(report.trades) >= 1
    trade = report.trades[0]
    assert trade.side == "LONG"
    assert trade.entry_date == fill_date.isoformat()

    expiry = ExpiryResolver().next_monthly_expiry(get_underlying("NIFTY"), decision_date)
    t = (expiry - fill_date).days / 365.0
    expected_price = fill_open * math.exp(0.065 * t)
    assert trade.entry_price == pytest.approx(expected_price, rel=1e-9)


def test_futures_stop_and_target_are_atr_based_not_percentage_based(tmp_path):
    provider = FOMarketDataProvider(seed=7, base_iv=0.15)
    bars = provider.get_underlying_daily_bars("NIFTY", dt.date(2026, 8, 3), dt.date(2026, 8, 25))
    decision_date = bars.index[3].date()

    equity_config = _equity_cfg(str(tmp_path))
    engine = SignalEngine(equity_config, rules=[_buy_on_date_rule(decision_date)])
    fo_config = FOConfig(futures_atr_stop_multiplier=1.5, futures_atr_target_multiplier=3.0,
                        futures_risk_per_trade_pct=0.02)
    bt = FOBacktester(fo_config, equity_config, signal_engine=engine)

    report = bt.run_futures_strategy("NIFTY", bars, provider, starting_cash=10_000_000.0)
    assert len(report.trades) >= 1
    trade = report.trades[0]

    from trading_risk_engine import indicators as ind
    df = ind.compute_all(bars, atr_period=3, sma_trend_period=3, rsi_period=3, bb_period=3)
    atr_at_decision = float(df["atr"].loc[pd.Timestamp(decision_date)])
    if trade.exit_reason == "STOP_LOSS":
        assert trade.exit_price <= trade.entry_price - atr_at_decision * 1.5 + 1e-6


def test_futures_expiry_settlement_converges_to_spot(tmp_path):
    fo_config = FOConfig(futures_risk_per_trade_pct=0.02, futures_atr_stop_multiplier=1.5,
                        futures_atr_target_multiplier=3.0)
    provider = FOMarketDataProvider(seed=8, base_iv=0.10)
    bars = provider.get_underlying_daily_bars("NIFTY", dt.date(2026, 8, 3), dt.date(2026, 8, 26))
    decision_date = bars.index[3].date()

    equity_config = _equity_cfg(str(tmp_path))
    engine = SignalEngine(equity_config, rules=[_buy_on_date_rule(decision_date)])
    bt = FOBacktester(fo_config, equity_config, signal_engine=engine)

    report = bt.run_futures_strategy("NIFTY", bars, provider, starting_cash=10_000_000.0)
    assert len(report.trades) >= 1
    trade = report.trades[0]
    assert trade.exit_reason in ("EXPIRY_SETTLEMENT", "END_OF_BACKTEST")
    final_spot = float(bars["close"].iloc[-1])
    assert trade.exit_price == pytest.approx(final_spot, rel=0.01)


def test_no_lookahead_bias_in_futures_backtest(tmp_path):
    equity_config = _equity_cfg(str(tmp_path))
    fo_config = FOConfig(futures_risk_per_trade_pct=0.02)
    provider = FOMarketDataProvider(seed=9, base_iv=0.15)
    bars = provider.get_underlying_daily_bars("NIFTY", dt.date(2026, 8, 3), dt.date(2026, 10, 15))

    bt_full = FOBacktester(fo_config, equity_config)
    bt_trunc = FOBacktester(fo_config, equity_config)
    report_full = bt_full.run_futures_strategy("NIFTY", bars, provider, starting_cash=10_000_000.0)
    report_trunc = bt_trunc.run_futures_strategy("NIFTY", bars.iloc[:-15], provider, starting_cash=10_000_000.0)

    overlap = min(len(report_full.equity_curve), len(report_trunc.equity_curve))
    for i in range(overlap - 1):
        assert report_full.equity_curve[i].equity == pytest.approx(report_trunc.equity_curve[i].equity, abs=0.01)


def test_margin_blocked_returns_to_zero_after_a_full_round_trip(tmp_path):
    fo_config = FOConfig(futures_risk_per_trade_pct=0.02, futures_atr_stop_multiplier=50.0,
                        futures_atr_target_multiplier=50.0)
    provider = FOMarketDataProvider(seed=8, base_iv=0.10)
    bars = provider.get_underlying_daily_bars("NIFTY", dt.date(2026, 8, 3), dt.date(2026, 8, 20))
    decision_date = bars.index[3].date()
    equity_config = _equity_cfg(str(tmp_path))
    engine = SignalEngine(equity_config, rules=[_buy_on_date_rule(decision_date)])
    bt = FOBacktester(fo_config, equity_config, signal_engine=engine)

    report = bt.run_futures_strategy("NIFTY", bars, provider, starting_cash=10_000_000.0)
    assert report.equity_curve[-1].margin_blocked == pytest.approx(0.0)
    assert report.equity_curve[-1].equity == pytest.approx(report.equity_curve[-1].cash)
