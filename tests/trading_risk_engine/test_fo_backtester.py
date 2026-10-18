import datetime as dt

import pandas as pd
import pytest

from trading_risk_engine.config import Config as EquityConfig
from trading_risk_engine.instruments.calendar import ExpiryResolver
from trading_risk_engine.instruments.contracts import OptionType
from trading_risk_engine.instruments.fo_backtester import ExpiryChoice, FOBacktester, OptionStrategyStyle
from trading_risk_engine.instruments.fo_config import FOConfig
from trading_risk_engine.instruments.fo_data_provider import STRIKE_INTERVALS, FOMarketDataProvider
from trading_risk_engine.options_pricing import black_scholes_price
from trading_risk_engine.instruments.underlyings import get_underlying
from trading_risk_engine.models import SignalDirection
from trading_risk_engine.signals import SignalEngine, SignalRule


def _equity_cfg(tmp_dir: str, **overrides) -> EquityConfig:
    defaults = dict(data_dir=tmp_dir, sma_trend_period=3, atr_period=3, rsi_period=3, bb_period=3,
                    macd_fast_period=2, macd_slow_period=3, macd_signal_period=2,
                    stoch_k_period=3, stoch_d_period=2)
    defaults.update(overrides)
    return EquityConfig(**defaults)


def _buy_on_date_rule(target_date: dt.date, direction: float = 1.0):
    def fn(df, cfg):
        if df.index[-1].date() == target_date:
            return direction, f"test trigger on {target_date}"
        return 0.0, None
    return SignalRule("test_trigger", 1.0, fn)


def _bars(rows: list, start="2026-08-03") -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=len(rows))
    df = pd.DataFrame(rows, index=dates)
    df.index.name = "date"
    df["volume"] = 100_000.0
    return df


@pytest.mark.parametrize("signal,style,expected_type,expected_side", [
    (SignalDirection.BUY, OptionStrategyStyle.DIRECTIONAL_LONG, OptionType.CALL, "LONG"),
    (SignalDirection.SELL, OptionStrategyStyle.DIRECTIONAL_LONG, OptionType.PUT, "LONG"),
    (SignalDirection.BUY, OptionStrategyStyle.INCOME_SHORT, OptionType.PUT, "SHORT"),
    (SignalDirection.SELL, OptionStrategyStyle.INCOME_SHORT, OptionType.CALL, "SHORT"),
])
def test_signal_translation_matrix(signal, style, expected_type, expected_side):
    action = FOBacktester._translate_signal_to_option_action(signal, style)
    assert action == (expected_type, expected_side)


def test_hold_signal_translates_to_no_action():
    assert FOBacktester._translate_signal_to_option_action(SignalDirection.HOLD, OptionStrategyStyle.DIRECTIONAL_LONG) is None


def test_entry_premium_matches_black_scholes_computed_independently(tmp_path):
    rows = [
        {"open": 24300.0, "high": 24350.0, "low": 24250.0, "close": 24300.0},
        {"open": 24300.0, "high": 24350.0, "low": 24250.0, "close": 24300.0},
        {"open": 24300.0, "high": 24400.0, "low": 24250.0, "close": 24350.0},
        {"open": 24400.0, "high": 24450.0, "low": 24380.0, "close": 24420.0},
        {"open": 24420.0, "high": 24460.0, "low": 24400.0, "close": 24440.0},
    ]
    bars = _bars(rows, start="2026-08-03")
    decision_date = bars.index[2].date()
    fill_date = bars.index[3].date()

    equity_config = _equity_cfg(str(tmp_path))
    engine = SignalEngine(equity_config, rules=[_buy_on_date_rule(decision_date)])
    fo_config = FOConfig(long_option_risk_per_trade_pct=0.02)
    bt = FOBacktester(fo_config, equity_config, signal_engine=engine)
    provider = FOMarketDataProvider(seed=1, annual_risk_free_rate=0.065, base_iv=0.14)

    report = bt.run_option_strategy("NIFTY", bars, provider, OptionStrategyStyle.DIRECTIONAL_LONG,
                                    expiry_choice=ExpiryChoice.WEEKLY, starting_cash=5_000_000.0)

    assert len(report.trades) >= 1
    trade = report.trades[0]
    assert trade.contract.option_type == OptionType.CALL
    assert trade.entry_date == fill_date.isoformat()

    decision_close = 24350.0
    interval = STRIKE_INTERVALS["NIFTY"]
    expected_strike = round(decision_close / interval) * interval
    expiry = ExpiryResolver().next_weekly_expiry(get_underlying("NIFTY"), decision_date)
    fill_open = 24400.0
    t = (expiry - fill_date).days / 365.0
    from trading_risk_engine.instruments.fo_data_provider import _iv_with_smile
    iv = _iv_with_smile(0.14, fill_open, expected_strike)
    expected_premium = black_scholes_price(fill_open, expected_strike, 0.065, iv, t, is_call=True)

    assert trade.contract.strike == expected_strike
    assert trade.contract.expiry == expiry
    assert trade.entry_price == pytest.approx(expected_premium, rel=1e-9)


def test_position_surviving_to_expiry_settles_at_intrinsic_value(tmp_path):
    equity_config = _equity_cfg(str(tmp_path))
    fo_config = FOConfig(long_option_risk_per_trade_pct=0.02, long_option_stop_loss_pct=0.99,
                         long_option_take_profit_pct=50.0)
    provider = FOMarketDataProvider(seed=2, annual_risk_free_rate=0.065, base_iv=0.12)

    bars = provider.get_underlying_daily_bars("NIFTY", dt.date(2026, 8, 3), dt.date(2026, 8, 14))
    decision_date = bars.index[0].date()
    engine = SignalEngine(equity_config, rules=[_buy_on_date_rule(decision_date)])
    bt = FOBacktester(fo_config, equity_config, signal_engine=engine)

    report = bt.run_option_strategy("NIFTY", bars, provider, OptionStrategyStyle.DIRECTIONAL_LONG,
                                    expiry_choice=ExpiryChoice.WEEKLY, starting_cash=10_000_000.0)

    assert len(report.trades) >= 1
    trade = report.trades[0]
    assert trade.exit_reason == "EXPIRY_SETTLEMENT"
    assert trade.exit_price >= 0.0


def test_weekly_expiry_choice_rejected_for_monthly_only_underlying(tmp_path):
    equity_config = _equity_cfg(str(tmp_path))
    fo_config = FOConfig()
    bt = FOBacktester(fo_config, equity_config)
    provider = FOMarketDataProvider(seed=1)
    bars = provider.get_underlying_daily_bars("BANKNIFTY", dt.date(2026, 8, 3), dt.date(2026, 8, 20))
    with pytest.raises(ValueError, match="no weekly options"):
        bt.run_option_strategy("BANKNIFTY", bars, provider, OptionStrategyStyle.DIRECTIONAL_LONG,
                               expiry_choice=ExpiryChoice.WEEKLY)


def test_no_lookahead_bias_in_options_backtest(tmp_path):
    equity_config = _equity_cfg(str(tmp_path))
    fo_config = FOConfig(long_option_risk_per_trade_pct=0.02)
    provider = FOMarketDataProvider(seed=4, base_iv=0.15)
    bars = provider.get_underlying_daily_bars("NIFTY", dt.date(2026, 8, 3), dt.date(2026, 10, 1))

    bt_full = FOBacktester(fo_config, equity_config)
    bt_truncated = FOBacktester(fo_config, equity_config)

    report_full = bt_full.run_option_strategy("NIFTY", bars, provider, OptionStrategyStyle.DIRECTIONAL_LONG,
                                              expiry_choice=ExpiryChoice.WEEKLY, starting_cash=5_000_000.0)
    report_trunc = bt_truncated.run_option_strategy("NIFTY", bars.iloc[:-15], provider,
                                                     OptionStrategyStyle.DIRECTIONAL_LONG,
                                                     expiry_choice=ExpiryChoice.WEEKLY, starting_cash=5_000_000.0)

    overlap = min(len(report_full.equity_curve), len(report_trunc.equity_curve))
    for i in range(overlap - 1):
        assert report_full.equity_curve[i].equity == pytest.approx(report_trunc.equity_curve[i].equity, abs=0.01)


# -- Regression: found by calling FOTradingEngine.run_futures_backtest in the merged project --
# FOTrade.r_multiple referenced a corrupted attribute name (phase6_eternal_phase6_risk_amount), so ANY
# backtest that produced at least one trade crashed with AttributeError once results were serialized.
def test_fo_trade_r_multiple_is_computed_from_risk_amount():
    from trading_risk_engine.instruments.fo_backtester import FOTrade
    t = FOTrade(contract=None, side="LONG", num_lots=1, entry_date="2026-01-01", exit_date="2026-01-02",
                entry_price=100.0, exit_price=110.0, exit_reason="TP", margin_required=1000.0,
                risk_amount=500.0, pnl=1000.0)
    assert t.r_multiple == pytest.approx(2.0)
    t0 = FOTrade(contract=None, side="LONG", num_lots=1, entry_date="a", exit_date="b", entry_price=1.0,
                 exit_price=1.0, exit_reason="x", margin_required=0.0, risk_amount=0.0, pnl=5.0)
    assert t0.r_multiple == 0.0
