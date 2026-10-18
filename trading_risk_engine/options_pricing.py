"""
Black-Scholes-Merton option pricing and Greeks.

Index options in India (NIFTY, SENSEX, BANKNIFTY, FINNIFTY) are
European-style and cash-settled (NSE's own product page: "Option type
identifies whether it is a call or a put option., CE - Call European, PE
- Put European") — no early exercise, no physical delivery — which is
exactly the assumption Black-Scholes-Merton is built on, making it the
right (not just convenient) model here, not an approximation of
convenience.

Implemented from the closed-form solution directly (`math.erf` for the
normal CDF, no scipy dependency — consistent with this project's general
preference for auditable, dependency-light math over an external
library's own internals, same reasoning as `indicators.py`).

Verified two ways, deliberately not relying on hand-arithmetic to five
decimal places for the Greeks (a real risk of MY OWN arithmetic errors
contaminating an "expected" value that then either fails a correct
implementation or validates a wrong one):

1. **Call/put price** against the textbook reference case (S=K=100,
   r=5%, sigma=20%, T=1 year) — this exact parameter set is the standard
   worked example in Hull's "Options, Futures, and Other Derivatives"
   and most finance texts, with a well-published answer (call ~= 10.45,
   put ~= 5.57), independent of this implementation.
2. **Mathematical identities that must hold for ANY valid inputs** —
   put-call parity (C - P = S - K*e^(-rT)) and the delta relationship
   (delta_call - delta_put = 1.0) are not approximations, they're
   consequences of the model's own no-arbitrage derivation, so testing
   them catches real implementation bugs without requiring precise
   hand-computed reference numbers for every Greek.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class OptionGreeks:
    delta: float
    gamma: float
    theta_per_day: float  # per CALENDAR day (annualized theta / 365) — see note in theta()
    vega_per_pct: float  # per 1 percentage-point change in volatility (annualized vega / 100)
    rho_per_pct: float  # per 1 percentage-point change in the risk-free rate


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)


def _d1_d2(spot: float, strike: float, risk_free_rate: float, volatility: float, time_to_expiry_years: float):
    if time_to_expiry_years <= 0 or volatility <= 0 or spot <= 0 or strike <= 0:
        return None, None
    sqrt_t = math.sqrt(time_to_expiry_years)
    d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * volatility ** 2) * time_to_expiry_years) / (
        volatility * sqrt_t
    )
    d2 = d1 - volatility * sqrt_t
    return d1, d2


def black_scholes_price(
    spot: float, strike: float, risk_free_rate: float, volatility: float,
    time_to_expiry_years: float, is_call: bool,
) -> float:
    """Theoretical premium for a European option. At or past expiry
    (time_to_expiry_years <= 0), returns pure intrinsic value —
    max(spot-strike, 0) for a call, max(strike-spot, 0) for a put — since
    there is no time value left to model, not because the formula breaks
    down. Same for volatility <= 0 (a degenerate, zero-uncertainty case)."""
    d1, d2 = _d1_d2(spot, strike, risk_free_rate, volatility, time_to_expiry_years)
    if d1 is None:
        intrinsic = max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)
        return intrinsic

    discounted_strike = strike * math.exp(-risk_free_rate * time_to_expiry_years)
    if is_call:
        return spot * _norm_cdf(d1) - discounted_strike * _norm_cdf(d2)
    return discounted_strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def black_scholes_greeks(
    spot: float, strike: float, risk_free_rate: float, volatility: float,
    time_to_expiry_years: float, is_call: bool,
) -> OptionGreeks:
    """Theta is reported per CALENDAR day (annualized theta / 365), not
    per trading day (/252) — a convention choice, not an error: an
    option's time value erodes every calendar day it sits closer to
    expiry, weekends included, which daily P&L tracking in
    `fo_backtester.py` needs to reflect accurately for weekend-spanning
    positions. Vega and rho are reported per ONE PERCENTAGE POINT of
    volatility/rate change (the conventional retail-facing units — "if
    IV rises 1%, this option gains approximately X") rather than per a
    full 100% change, which the raw formula produces."""
    d1, d2 = _d1_d2(spot, strike, risk_free_rate, volatility, time_to_expiry_years)
    if d1 is None:
        if is_call:
            delta = 1.0 if spot > strike else (0.5 if spot == strike else 0.0)
        else:
            delta = -1.0 if spot < strike else (-0.5 if spot == strike else 0.0)
        return OptionGreeks(delta=delta, gamma=0.0, theta_per_day=0.0, vega_per_pct=0.0, rho_per_pct=0.0)

    pdf_d1 = _norm_pdf(d1)
    sqrt_t = math.sqrt(time_to_expiry_years)
    discounted_strike = strike * math.exp(-risk_free_rate * time_to_expiry_years)

    delta = _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0
    gamma = pdf_d1 / (spot * volatility * sqrt_t)
    vega_annual = spot * pdf_d1 * sqrt_t
    vega_per_pct = vega_annual / 100.0

    time_decay_term = -(spot * pdf_d1 * volatility) / (2.0 * sqrt_t)
    if is_call:
        theta_annual = time_decay_term - risk_free_rate * discounted_strike * _norm_cdf(d2)
        rho_annual = discounted_strike * time_to_expiry_years * _norm_cdf(d2)  # K*T*e^(-rT)*N(d2)
    else:
        theta_annual = time_decay_term + risk_free_rate * discounted_strike * _norm_cdf(-d2)
        rho_annual = -discounted_strike * time_to_expiry_years * _norm_cdf(-d2)  # -K*T*e^(-rT)*N(-d2)

    return OptionGreeks(
        delta=delta, gamma=gamma,
        theta_per_day=theta_annual / 365.0,
        vega_per_pct=vega_per_pct,
        rho_per_pct=rho_annual / 100.0,
    )
