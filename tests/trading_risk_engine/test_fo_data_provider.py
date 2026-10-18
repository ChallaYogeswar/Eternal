import datetime as dt
import math

import numpy as np
import pytest

from trading_risk_engine.instruments.fo_data_provider import FOMarketDataProvider, STRIKE_INTERVALS
from trading_risk_engine.instruments.contracts import FutureContract, OptionContract, OptionType


@pytest.fixture
def provider():
    return FOMarketDataProvider(seed=1, annual_risk_free_rate=0.065, base_iv=0.14)


def test_underlying_bars_have_the_standard_ohlcv_shape(provider):
    bars = provider.get_underlying_daily_bars("NIFTY", dt.date(2026, 1, 1), dt.date(2026, 3, 1))
    assert {"open", "high", "low", "close", "volume"}.issubset(set(bars.columns))
    assert len(bars) > 0


def test_underlying_bars_reject_unknown_symbol(provider):
    with pytest.raises(ValueError, match="unknown underlying"):
        provider.get_underlying_daily_bars("DOWJONES", dt.date(2026, 1, 1), dt.date(2026, 3, 1))


def test_underlying_bars_are_deterministic_for_same_seed():
    p1 = FOMarketDataProvider(seed=5)
    p2 = FOMarketDataProvider(seed=5)
    b1 = p1.get_underlying_daily_bars("SENSEX", dt.date(2026, 1, 1), dt.date(2026, 2, 1))
    b2 = p2.get_underlying_daily_bars("SENSEX", dt.date(2026, 1, 1), dt.date(2026, 2, 1))
    import pandas as pd
    pd.testing.assert_frame_equal(b1, b2)


def test_price_option_at_expiry_is_intrinsic_value(provider):
    contract = OptionContract(underlying_symbol="NIFTY", strike=24000, expiry=dt.date(2026, 8, 18),
                              option_type=OptionType.CALL)
    quote = provider.price_option(contract, as_of=dt.date(2026, 8, 18), spot=24500.0)
    assert quote.theoretical_price == pytest.approx(500.0)


def test_price_option_time_to_expiry_shrinks_approaching_expiry(provider):
    contract = OptionContract(underlying_symbol="NIFTY", strike=24500, expiry=dt.date(2026, 8, 18),
                              option_type=OptionType.CALL)
    far = provider.price_option(contract, as_of=dt.date(2026, 7, 1), spot=24350.0)
    near = provider.price_option(contract, as_of=dt.date(2026, 8, 15), spot=24350.0)
    assert near.theoretical_price < far.theoretical_price


def test_smile_gives_otm_strikes_higher_iv_than_atm(provider):
    expiry = dt.date(2026, 8, 18)
    atm = OptionContract(underlying_symbol="NIFTY", strike=24350, expiry=expiry, option_type=OptionType.CALL)
    otm = OptionContract(underlying_symbol="NIFTY", strike=25500, expiry=expiry, option_type=OptionType.CALL)
    atm_quote = provider.price_option(atm, as_of=dt.date(2026, 7, 1), spot=24350.0)
    otm_quote = provider.price_option(otm, as_of=dt.date(2026, 7, 1), spot=24350.0)
    assert otm_quote.implied_volatility > atm_quote.implied_volatility


def test_smile_is_symmetric_for_equidistant_strikes(provider):
    expiry = dt.date(2026, 8, 18)
    spot = 24350.0
    lower = OptionContract(underlying_symbol="NIFTY", strike=spot - 500, expiry=expiry, option_type=OptionType.PUT)
    upper = OptionContract(underlying_symbol="NIFTY", strike=spot + 500, expiry=expiry, option_type=OptionType.CALL)
    lower_quote = provider.price_option(lower, as_of=dt.date(2026, 7, 1), spot=spot)
    upper_quote = provider.price_option(upper, as_of=dt.date(2026, 7, 1), spot=spot)
    assert lower_quote.implied_volatility == pytest.approx(upper_quote.implied_volatility, rel=1e-9)


def test_future_price_matches_cost_of_carry_formula_hand_calculation(provider):
    contract = FutureContract(underlying_symbol="NIFTY", expiry=dt.date(2026, 8, 18))
    as_of = dt.date(2026, 5, 19)
    quote = provider.price_future(contract, as_of=as_of, spot=24350.0)
    t = (contract.expiry - as_of).days / 365.0
    expected = 24350.0 * math.exp(0.065 * t)
    assert quote.theoretical_price == pytest.approx(expected, rel=1e-9)


def test_future_basis_is_positive_for_normal_contango(provider):
    contract = FutureContract(underlying_symbol="NIFTY", expiry=dt.date(2026, 8, 18))
    quote = provider.price_future(contract, as_of=dt.date(2026, 5, 19), spot=24350.0)
    assert quote.basis > 0


def test_future_price_equals_spot_at_expiry(provider):
    contract = FutureContract(underlying_symbol="NIFTY", expiry=dt.date(2026, 8, 18))
    quote = provider.price_future(contract, as_of=dt.date(2026, 8, 18), spot=24350.0)
    assert quote.theoretical_price == pytest.approx(24350.0)


def test_option_chain_has_expected_number_of_strikes(provider):
    chain = provider.get_option_chain("NIFTY", as_of=dt.date(2026, 7, 1), spot=24350.0,
                                      expiry=dt.date(2026, 8, 18), num_strikes_each_side=5)
    assert len(chain) == 11 * 2


def test_option_chain_atm_strike_rounds_to_the_correct_interval(provider):
    chain = provider.get_option_chain("NIFTY", as_of=dt.date(2026, 7, 1), spot=24365.0,
                                      expiry=dt.date(2026, 8, 18), num_strikes_each_side=1)
    strikes = sorted({q.contract.strike for q in chain})
    assert 24350 in strikes


def test_option_chain_all_strikes_are_multiples_of_the_instrument_interval(provider):
    chain = provider.get_option_chain("BANKNIFTY", as_of=dt.date(2026, 7, 1), spot=52180.0,
                                      expiry=dt.date(2026, 8, 25), num_strikes_each_side=8)
    interval = STRIKE_INTERVALS["BANKNIFTY"]
    for quote in chain:
        assert quote.contract.strike % interval == 0


def test_option_chain_contains_both_call_and_put_for_every_strike(provider):
    chain = provider.get_option_chain("NIFTY", as_of=dt.date(2026, 7, 1), spot=24350.0,
                                      expiry=dt.date(2026, 8, 18), num_strikes_each_side=3)
    strikes = {q.contract.strike for q in chain}
    for strike in strikes:
        types_at_strike = {q.contract.option_type for q in chain if q.contract.strike == strike}
        assert types_at_strike == {OptionType.CALL, OptionType.PUT}


def test_underlying_bars_use_realistic_starting_levels_per_index(provider):
    # Regression test for a real bug found while building this: a shared
    # provider instance with one generic start_price (1000.0) produced
    # NIFTY strikes around "1050" instead of NIFTY's actual ~24,000-point
    # scale. Each underlying must get ITS OWN realistic starting level.
    nifty_bars = provider.get_underlying_daily_bars("NIFTY", dt.date(2026, 1, 2), dt.date(2026, 1, 10))
    sensex_bars = provider.get_underlying_daily_bars("SENSEX", dt.date(2026, 1, 2), dt.date(2026, 1, 10))
    assert nifty_bars["close"].iloc[0] > 15_000  # NIFTY-scale, not 1000-scale
    assert sensex_bars["close"].iloc[0] > 50_000  # SENSEX-scale
    assert sensex_bars["close"].iloc[0] > nifty_bars["close"].iloc[0]  # SENSEX trades meaningfully higher than NIFTY
