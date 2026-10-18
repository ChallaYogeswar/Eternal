"""
Testing strategy explained in options_pricing.py's own module docstring:
call/put price checked against a well-published textbook reference case
(not this module's own output), everything else checked against
mathematical identities that MUST hold for any correct implementation —
put-call parity and the delta relationship in particular — rather than
hand-computed Greek values to five decimal places, which risks the
test's own arithmetic being the source of an error.
"""
import math

import pytest

from trading_risk_engine.options_pricing import black_scholes_greeks, black_scholes_price


def test_call_price_matches_textbook_reference_case():
    price = black_scholes_price(spot=100, strike=100, risk_free_rate=0.05, volatility=0.20,
                                time_to_expiry_years=1.0, is_call=True)
    assert price == pytest.approx(10.45, abs=0.01)


def test_put_price_matches_textbook_reference_case():
    price = black_scholes_price(spot=100, strike=100, risk_free_rate=0.05, volatility=0.20,
                                time_to_expiry_years=1.0, is_call=False)
    assert price == pytest.approx(5.57, abs=0.01)


@pytest.mark.parametrize("spot,strike,r,vol,t", [
    (100, 100, 0.05, 0.20, 1.0),
    (24350, 24000, 0.065, 0.14, 0.25),
    (77900, 80000, 0.065, 0.16, 0.019),
    (50000, 45000, 0.065, 0.30, 2.0),
    (10, 100, 0.05, 0.50, 0.5),
])
def test_put_call_parity_holds(spot, strike, r, vol, t):
    call = black_scholes_price(spot, strike, r, vol, t, is_call=True)
    put = black_scholes_price(spot, strike, r, vol, t, is_call=False)
    lhs = call - put
    rhs = spot - strike * math.exp(-r * t)
    assert lhs == pytest.approx(rhs, abs=0.01)


@pytest.mark.parametrize("spot,strike,r,vol,t", [
    (100, 100, 0.05, 0.20, 1.0),
    (24350, 24000, 0.065, 0.14, 0.25),
    (77900, 80000, 0.065, 0.16, 0.019),
])
def test_delta_call_minus_delta_put_is_one(spot, strike, r, vol, t):
    call_greeks = black_scholes_greeks(spot, strike, r, vol, t, is_call=True)
    put_greeks = black_scholes_greeks(spot, strike, r, vol, t, is_call=False)
    assert (call_greeks.delta - put_greeks.delta) == pytest.approx(1.0, abs=1e-9)


def test_gamma_and_vega_are_identical_for_call_and_put():
    call_greeks = black_scholes_greeks(24350, 24000, 0.065, 0.14, 0.25, is_call=True)
    put_greeks = black_scholes_greeks(24350, 24000, 0.065, 0.14, 0.25, is_call=False)
    assert call_greeks.gamma == pytest.approx(put_greeks.gamma, rel=1e-9)
    assert call_greeks.vega_per_pct == pytest.approx(put_greeks.vega_per_pct, rel=1e-9)


def test_at_expiry_price_is_pure_intrinsic_value():
    itm_call = black_scholes_price(24500, 24000, 0.065, 0.14, 0.0, is_call=True)
    otm_call = black_scholes_price(23500, 24000, 0.065, 0.14, 0.0, is_call=True)
    itm_put = black_scholes_price(23500, 24000, 0.065, 0.14, 0.0, is_call=False)
    assert itm_call == pytest.approx(500.0)
    assert otm_call == pytest.approx(0.0)
    assert itm_put == pytest.approx(500.0)


def test_at_expiry_greeks_have_no_time_value_left():
    greeks = black_scholes_greeks(24500, 24000, 0.065, 0.14, 0.0, is_call=True)
    assert greeks.gamma == 0.0
    assert greeks.theta_per_day == 0.0
    assert greeks.vega_per_pct == 0.0
    assert greeks.delta == 1.0


def test_deep_itm_call_delta_approaches_one():
    greeks = black_scholes_greeks(spot=50000, strike=10000, risk_free_rate=0.065, volatility=0.15,
                                  time_to_expiry_years=0.1, is_call=True)
    assert greeks.delta > 0.99


def test_deep_otm_call_delta_approaches_zero():
    greeks = black_scholes_greeks(spot=10000, strike=50000, risk_free_rate=0.065, volatility=0.15,
                                  time_to_expiry_years=0.1, is_call=True)
    assert greeks.delta < 0.01


def test_gamma_always_non_negative():
    for spot in (20000, 24000, 28000):
        greeks = black_scholes_greeks(spot, 24000, 0.065, 0.15, 0.25, is_call=True)
        assert greeks.gamma >= 0.0


def test_vega_always_non_negative_for_positive_time():
    for spot in (20000, 24000, 28000):
        greeks = black_scholes_greeks(spot, 24000, 0.065, 0.15, 0.25, is_call=True)
        assert greeks.vega_per_pct >= 0.0


def test_theta_is_negative_for_a_long_atm_option_normal_conditions():
    greeks = black_scholes_greeks(24350, 24350, 0.065, 0.14, 0.05, is_call=True)
    assert greeks.theta_per_day < 0.0


def test_degenerate_zero_volatility_does_not_crash():
    price = black_scholes_price(24350, 24000, 0.065, 0.0, 0.25, is_call=True)
    assert price >= 0.0
    greeks = black_scholes_greeks(24350, 24000, 0.065, 0.0, 0.25, is_call=True)
    assert greeks.delta in (0.0, 0.5, 1.0)
