import datetime as dt

import pytest

from trading_risk_engine.instruments.contracts import FutureContract, OptionContract, OptionType
from trading_risk_engine.instruments.fo_config import FOConfig
from trading_risk_engine.instruments.fo_risk import FORiskManager


def _nifty_call(strike=24500, expiry=dt.date(2026, 8, 18)):
    return OptionContract(underlying_symbol="NIFTY", strike=strike, expiry=expiry, option_type=OptionType.CALL)


def _nifty_future(expiry=dt.date(2026, 8, 18)):
    return FutureContract(underlying_symbol="NIFTY", expiry=expiry)


def _rm(**overrides):
    return FORiskManager(FOConfig(**overrides))


def test_size_long_option_hand_calculation():
    rm = _rm(long_option_risk_per_trade_pct=0.01, long_option_stop_loss_pct=0.50, long_option_take_profit_pct=1.00)
    decision = rm.size_long_option(_nifty_call(), entry_premium=150.0, spot=24350.0, account_equity=500_000.0)
    assert decision.num_lots == 1
    assert decision.stop_price == pytest.approx(75.0)
    assert decision.take_profit_price == pytest.approx(300.0)
    assert decision.margin_required == pytest.approx(150.0 * 65)


def test_long_option_stop_is_below_entry_premium():
    rm = _rm()
    decision = rm.size_long_option(_nifty_call(), entry_premium=150.0, spot=24350.0, account_equity=2_000_000.0)
    assert decision.stop_price < 150.0 < decision.take_profit_price


def test_long_option_zero_lots_when_risk_budget_too_small():
    rm = _rm(long_option_risk_per_trade_pct=0.01)
    decision = rm.size_long_option(_nifty_call(), entry_premium=150.0, spot=24350.0, account_equity=1_000.0)
    assert decision.num_lots == 0
    assert any("no trade" in r for r in decision.reasons)


def test_long_option_nan_premium_returns_zero_lots_not_a_crash():
    rm = _rm()
    decision = rm.size_long_option(_nifty_call(), entry_premium=float("nan"), spot=24350.0, account_equity=500_000.0)
    assert decision.num_lots == 0


def test_size_short_option_hand_calculation():
    rm = _rm(short_option_risk_per_trade_pct=0.01, short_option_stop_loss_multiple=2.5,
            short_option_take_profit_pct=0.50)
    decision = rm.size_short_option(_nifty_call(), entry_premium=150.0, spot=24350.0, account_equity=2_000_000.0)
    assert decision.num_lots == 1
    assert decision.stop_price == pytest.approx(375.0)
    assert decision.take_profit_price == pytest.approx(75.0)


def test_short_option_stop_is_above_entry_premium():
    rm = _rm()
    decision = rm.size_short_option(_nifty_call(), entry_premium=150.0, spot=24350.0, account_equity=5_000_000.0)
    assert decision.take_profit_price < 150.0 < decision.stop_price


def test_short_option_nan_equity_returns_zero_lots_not_a_crash():
    rm = _rm()
    decision = rm.size_short_option(_nifty_call(), entry_premium=150.0, spot=24350.0, account_equity=float("nan"))
    assert decision.num_lots == 0


def test_short_option_sized_from_loss_at_stop_not_premium_received():
    rm = _rm(short_option_risk_per_trade_pct=0.01)
    nifty_decision = rm.size_short_option(
        OptionContract(underlying_symbol="NIFTY", strike=24500, expiry=dt.date(2026, 8, 18), option_type=OptionType.CALL),
        entry_premium=150.0, spot=24350.0, account_equity=5_000_000.0,
    )
    midcap_decision = rm.size_short_option(
        OptionContract(underlying_symbol="MIDCPNIFTY", strike=24500, expiry=dt.date(2026, 8, 18), option_type=OptionType.CALL),
        entry_premium=150.0, spot=24350.0, account_equity=5_000_000.0,
    )
    assert nifty_decision.num_lots != midcap_decision.num_lots


def test_size_future_long_hand_calculation():
    rm = _rm(futures_risk_per_trade_pct=0.01, futures_atr_stop_multiplier=1.5, futures_atr_target_multiplier=3.0)
    decision = rm.size_future(_nifty_future(), "LONG", entry_price=24400.0, atr_value=150.0,
                              account_equity=2_000_000.0)
    assert decision.num_lots == 1
    assert decision.stop_price == pytest.approx(24400.0 - 225.0)
    assert decision.take_profit_price == pytest.approx(24400.0 + 450.0)


def test_size_future_short_stop_and_target_are_mirrored():
    rm = _rm(futures_atr_stop_multiplier=1.5, futures_atr_target_multiplier=3.0)
    decision = rm.size_future(_nifty_future(), "SHORT", entry_price=24400.0, atr_value=150.0,
                              account_equity=2_000_000.0)
    assert decision.stop_price == pytest.approx(24400.0 + 225.0)
    assert decision.take_profit_price == pytest.approx(24400.0 - 450.0)


def test_future_invalid_atr_returns_zero_lots_not_a_crash():
    rm = _rm()
    d1 = rm.size_future(_nifty_future(), "LONG", entry_price=24400.0, atr_value=0.0, account_equity=2_000_000.0)
    d2 = rm.size_future(_nifty_future(), "LONG", entry_price=24400.0, atr_value=float("nan"), account_equity=2_000_000.0)
    assert d1.num_lots == 0
    assert d2.num_lots == 0


def test_margin_cap_reduces_lots_below_the_risk_based_figure():
    rm = _rm(short_option_risk_per_trade_pct=0.01, short_option_stop_loss_multiple=2.5,
            max_margin_utilization_pct=0.10, max_lots_per_position=50)
    decision = rm.size_short_option(_nifty_call(), entry_premium=50.0, spot=24350.0, account_equity=5_000_000.0)
    assert decision.num_lots == 2
    assert any("max_margin_utilization_pct" in r for r in decision.reasons)


def test_existing_margin_usage_reduces_available_room():
    rm = _rm(short_option_risk_per_trade_pct=0.01, short_option_stop_loss_multiple=2.5,
            max_margin_utilization_pct=0.10, max_lots_per_position=50)
    decision = rm.size_short_option(_nifty_call(), entry_premium=50.0, spot=24350.0, account_equity=5_000_000.0,
                                    current_margin_used=450_000.0)
    assert decision.num_lots == 0


def test_max_lots_per_position_cap():
    rm = _rm(long_option_risk_per_trade_pct=0.01, long_option_stop_loss_pct=0.50, max_lots_per_position=20)
    decision = rm.size_long_option(_nifty_call(), entry_premium=10.0, spot=24350.0, account_equity=10_000_000.0)
    assert decision.num_lots == 20
    assert any("max_lots_per_position" in r for r in decision.reasons)


def test_can_open_new_position_respects_concurrent_cap():
    rm = _rm(max_concurrent_fo_positions=3)
    assert rm.can_open_new_position(2) is True
    assert rm.can_open_new_position(3) is False


def test_fo_config_rejects_risk_above_two_percent():
    with pytest.raises(ValueError, match="2% hard ceiling"):
        FOConfig(long_option_risk_per_trade_pct=0.03)
    with pytest.raises(ValueError, match="2% hard ceiling"):
        FOConfig(short_option_risk_per_trade_pct=0.05)
    with pytest.raises(ValueError, match="2% hard ceiling"):
        FOConfig(futures_risk_per_trade_pct=0.10)


def test_fo_config_rejects_dangerously_high_margin_utilization():
    with pytest.raises(ValueError, match="dangerously high"):
        FOConfig(max_margin_utilization_pct=0.90)


def test_fo_config_accepts_reasonable_defaults():
    config = FOConfig()
    assert config.max_margin_utilization_pct <= 0.75
