import datetime as dt

import pytest

from trading_risk_engine.instruments.contracts import FOPosition, FutureContract, OptionContract, OptionType
from trading_risk_engine.instruments.margin import MarginConfig, future_margin_required, option_margin_required


def test_option_contract_display_symbol_format():
    c = OptionContract(underlying_symbol="NIFTY", strike=24500, expiry=dt.date(2026, 8, 18),
                       option_type=OptionType.CALL)
    assert c.display_symbol == "NIFTY26AUG24500CE"


def test_option_contract_display_symbol_handles_fractional_strike():
    c = OptionContract(underlying_symbol="BANKNIFTY", strike=52150.5, expiry=dt.date(2026, 11, 24),
                       option_type=OptionType.PUT)
    assert "52150.5" in c.display_symbol
    assert c.display_symbol.endswith("PE")


def test_future_contract_display_symbol_format():
    f = FutureContract(underlying_symbol="SENSEX", expiry=dt.date(2026, 3, 25))
    assert f.display_symbol == "SENSEX26MARFUT"


def test_option_contract_lot_size_derived_from_underlying():
    c = OptionContract(underlying_symbol="NIFTY", strike=24500, expiry=dt.date(2026, 8, 18),
                       option_type=OptionType.CALL)
    assert c.lot_size == 65


def test_is_expired():
    c = OptionContract(underlying_symbol="NIFTY", strike=24500, expiry=dt.date(2026, 8, 18),
                       option_type=OptionType.CALL)
    assert c.is_expired(dt.date(2026, 8, 19)) is True
    assert c.is_expired(dt.date(2026, 8, 18)) is False
    assert c.is_expired(dt.date(2026, 8, 17)) is False


def _position(side: str, num_lots: int = 2) -> FOPosition:
    contract = OptionContract(underlying_symbol="NIFTY", strike=24500, expiry=dt.date(2026, 8, 18),
                              option_type=OptionType.CALL)
    return FOPosition(contract=contract, side=side, num_lots=num_lots, entry_price=150.0,
                      entry_date="2026-08-10", margin_blocked=20000.0,
                      stop_loss_price=100.0, take_profit_price=250.0)


def test_position_quantity_is_lots_times_lot_size():
    pos = _position("LONG", num_lots=3)
    assert pos.quantity == 3 * 65


def test_position_signed_quantity_long_is_positive():
    pos = _position("LONG", num_lots=2)
    assert pos.signed_quantity == 2 * 65


def test_position_signed_quantity_short_is_negative():
    pos = _position("SHORT", num_lots=2)
    assert pos.signed_quantity == -2 * 65


def test_position_is_option_flag():
    option_pos = _position("LONG")
    future_pos = FOPosition(
        contract=FutureContract(underlying_symbol="NIFTY", expiry=dt.date(2026, 8, 18)),
        side="LONG", num_lots=1, entry_price=24500.0, entry_date="2026-08-10",
        margin_blocked=50000.0, stop_loss_price=24000.0, take_profit_price=25500.0,
    )
    assert option_pos.is_option is True
    assert future_pos.is_option is False


def test_long_option_margin_is_exactly_premium_times_quantity():
    c = OptionContract(underlying_symbol="NIFTY", strike=24500, expiry=dt.date(2026, 8, 18),
                       option_type=OptionType.CALL)
    margin = option_margin_required(c, side="LONG", num_lots=2, premium=150.0, spot_price=24350.0)
    assert margin == pytest.approx(150.0 * 2 * 65)


def test_short_option_margin_uses_notional_percentage_approximation():
    c = OptionContract(underlying_symbol="NIFTY", strike=24500, expiry=dt.date(2026, 8, 18),
                       option_type=OptionType.PUT)
    cfg = MarginConfig(approx_option_sell_margin_pct=0.14, min_absolute_margin_per_lot=15_000.0)
    margin = option_margin_required(c, side="SHORT", num_lots=1, premium=150.0, spot_price=24350.0, margin_config=cfg)
    notional = 24350.0 * 65
    expected = max(notional * 0.14, 15_000.0)
    assert margin == pytest.approx(expected)


def test_short_option_margin_respects_the_floor_for_a_low_notional_case():
    cfg = MarginConfig(approx_option_sell_margin_pct=0.01, min_absolute_margin_per_lot=15_000.0)
    c = OptionContract(underlying_symbol="NIFTY", strike=100, expiry=dt.date(2026, 8, 18), option_type=OptionType.PUT)
    margin = option_margin_required(c, side="SHORT", num_lots=1, premium=1.0, spot_price=100.0, margin_config=cfg)
    assert margin == pytest.approx(15_000.0)


def test_future_margin_uses_notional_percentage_approximation():
    f = FutureContract(underlying_symbol="NIFTY", expiry=dt.date(2026, 8, 18))
    cfg = MarginConfig(approx_futures_margin_pct=0.12, min_absolute_margin_per_lot=15_000.0)
    margin = future_margin_required(f, num_lots=1, futures_price=24400.0, margin_config=cfg)
    notional = 24400.0 * 65
    assert margin == pytest.approx(max(notional * 0.12, 15_000.0))


def test_margin_scales_with_number_of_lots():
    c = OptionContract(underlying_symbol="NIFTY", strike=24500, expiry=dt.date(2026, 8, 18),
                       option_type=OptionType.CALL)
    one_lot = option_margin_required(c, side="LONG", num_lots=1, premium=150.0, spot_price=24350.0)
    three_lots = option_margin_required(c, side="LONG", num_lots=3, premium=150.0, spot_price=24350.0)
    assert three_lots == pytest.approx(one_lot * 3)
