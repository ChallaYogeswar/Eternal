"""
Technical indicators — Eternal_Master_Build_Plan.md Section 7.2:
"pandas + pandas-ta/TA-Lib (RSI, MACD, Bollinger Bands, ATR)."

Deliberately hand-implemented rather than delegating to pandas-ta or
TA-Lib, for three concrete reasons, not a style preference:

1. Auditability. Build plan Section 7.2 is explicit: "Signals as
   explicit, auditable rules, not a black box." An indicator library
   whose exact smoothing convention you haven't personally verified is
   itself a small black box sitting underneath your signals. Every
   formula below is implemented as a plain loop over Python floats,
   documented against its textbook definition, and unit-tested against
   numbers worked out by hand (tests/test_indicators.py) — not against
   this module's own output, which would prove nothing.
2. A real compatibility risk, checked directly rather than assumed:
   pandas-ta has a well-known history of breaking under numpy>=2.0
   (older releases reference the removed `numpy.NaN` alias). The
   specific version available at build time (0.4.71b0) was tested here
   and does import and run correctly against numpy 2.5.1 / pandas 3.0.5
   — so this is not a "pandas-ta is broken" claim — but pinning your
   signal engine's core math to a dependency's internal smoothing
   convention, verified once and never re-checked, is a fragile
   foundation for the phase you explicitly asked to be the most careful
   about.
3. TA-Lib requires a compiled C library that isn't installable via pip
   alone and isn't available in this sandbox to even attempt.

Every function takes and returns plain pandas Series so it composes
normally with a DataFrame of OHLCV data; every function accepts a
`period` parameter rather than hardcoding one, but the *formula*, not the
period, is what changes between assets or timeframes — using period=14
in production and period=3 in a unit test (see the test file) exercises
the exact same code path, since the recursive smoothing logic is
period-agnostic.

Convention: insufficient warm-up data produces float('nan'), never a
silently wrong number and never a crash. Callers (signals.py,
backtester.py) treat NaN as "no opinion yet," not as zero or a bullish/
bearish default.
"""
from __future__ import annotations

import math
from typing import List, Tuple

import pandas as pd


def sma(values: pd.Series, period: int) -> pd.Series:
    """Simple moving average — the unweighted mean of the trailing
    `period` values. Bollinger Bands' middle band is this function
    directly, not a separate implementation."""
    return values.rolling(window=period, min_periods=period).mean()


def ema(values: pd.Series, period: int) -> pd.Series:
    """Exponential moving average, seeded with an N-period SMA (the
    textbook convention — see Wilder 1978 and most standard references),
    then smoothed recursively:

        EMA[0..period-2]  = NaN (insufficient data)
        EMA[period-1]     = SMA(values[0:period])                (seed)
        EMA[t]            = values[t] * alpha + EMA[t-1] * (1 - alpha)
        alpha             = 2 / (period + 1)

    Deliberately NOT pandas' `.ewm(adjust=False)`, which seeds with the
    single first observation rather than an N-period average — a
    different (and for a short warm-up window, meaningfully different)
    convention. See tests/test_indicators.py::test_ema_matches_hand_
    calculation for the worked example this implements.
    """
    n = len(values)
    out = [float("nan")] * n
    if n < period:
        return pd.Series(out, index=values.index)
    alpha = 2.0 / (period + 1)
    seed = values.iloc[0:period].mean()
    out[period - 1] = seed
    prev = seed
    for t in range(period, n):
        prev = values.iloc[t] * alpha + prev * (1 - alpha)
        out[t] = prev
    return pd.Series(out, index=values.index)


def rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index, Wilder's original smoothing (Wilder,
    "New Concepts in Technical Trading Systems", 1978) — NOT the
    simple-moving-average variant some libraries default to, which
    produces materially different values.

        gain[t] = max(close[t] - close[t-1], 0)
        loss[t] = max(close[t-1] - close[t], 0)
        avg_gain[period] = mean(gain[1..period])                 (seed)
        avg_loss[period] = mean(loss[1..period])                 (seed)
        avg_gain[t] = (avg_gain[t-1] * (period-1) + gain[t]) / period
        avg_loss[t] = (avg_loss[t-1] * (period-1) + loss[t]) / period
        RS  = avg_gain / avg_loss
        RSI = 100 - 100 / (1 + RS)

    Edge cases, both deliberate: avg_loss == 0 and avg_gain > 0 -> RSI =
    100 (nothing but gains in the window — genuinely maximal, not a
    division-by-zero crash). avg_gain == 0 and avg_loss == 0 -> RSI = 50
    (no price movement at all in the window is neither overbought nor
    oversold, not "infinitely strong").
    """
    n = len(closes)
    out = [float("nan")] * n
    if n <= period:
        return pd.Series(out, index=closes.index)

    deltas = closes.diff()
    gains = deltas.clip(lower=0.0)
    losses = (-deltas).clip(lower=0.0)

    avg_gain = gains.iloc[1:period + 1].mean()
    avg_loss = losses.iloc[1:period + 1].mean()
    out[period] = _rsi_from_avgs(avg_gain, avg_loss)

    for t in range(period + 1, n):
        g = gains.iloc[t]
        l = losses.iloc[t]
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        out[t] = _rsi_from_avgs(avg_gain, avg_loss)

    return pd.Series(out, index=closes.index)


def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    if avg_gain == 0.0 and avg_loss == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    closes: pd.Series, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Moving Average Convergence/Divergence (Appel).

        macd_line      = EMA(closes, fast) - EMA(closes, slow)
        signal_line    = EMA(macd_line, signal)
        histogram      = macd_line - signal_line

    Returns (macd_line, signal_line, histogram), each a pd.Series aligned
    to `closes`'s index. The signal line's own EMA is seeded from
    macd_line's first `signal_period` NON-NaN values — this module's
    `ema()` handles a leading run of NaNs correctly since it only looks
    at `len(values)` and iloc-based windows, not the index labels.
    """
    ema_fast = ema(closes, fast_period)
    ema_slow = ema(closes, slow_period)
    macd_line = ema_fast - ema_slow

    # macd_line has NaN for its first (slow_period - 1) entries; slice
    # those off before seeding the signal line's own EMA so its warm-up
    # window is `signal_period` real values, not `signal_period` NaNs.
    first_valid = macd_line.first_valid_index()
    if first_valid is None:
        nan_series = pd.Series([float("nan")] * len(closes), index=closes.index)
        return macd_line, nan_series, nan_series

    valid_tail = macd_line.loc[first_valid:]
    signal_tail = ema(valid_tail, signal_period)
    signal_line = pd.Series([float("nan")] * len(closes), index=closes.index)
    signal_line.loc[signal_tail.index] = signal_tail.values

    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(
    closes: pd.Series, period: int = 20, num_std: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands (Bollinger). Middle band is the plain SMA; the
    bands are +/- `num_std` POPULATION standard deviations (ddof=0) of
    the same trailing window — Bollinger's own original definition, and
    the convention this module tests against, not the sample standard
    deviation (ddof=1) some libraries default to. The difference is
    small but not zero, and "small but not zero" is exactly the kind of
    silent discrepancy this module exists to avoid.

    Returns (middle, upper, lower).
    """
    middle = sma(closes, period)
    std = closes.rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + num_std * std
    lower = middle - num_std * std
    return middle, upper, lower


def atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range (Wilder, 1978) — the volatility measure
    risk.py's position sizing and stop/target placement are built on.

        TR[0] = high[0] - low[0]                          (no prior close)
        TR[t] = max(high[t]-low[t], |high[t]-close[t-1]|, |low[t]-close[t-1]|)
        ATR[period-1] = mean(TR[0..period-1])                     (seed)
        ATR[t] = (ATR[t-1] * (period-1) + TR[t]) / period

    Same Wilder recursive-smoothing shape as RSI's averages above —
    deliberately, since that consistency is the point of citing "Wilder,
    1978" for both rather than mixing smoothing conventions across
    indicators without saying so.
    """
    n = len(highs)
    tr = [0.0] * n
    tr[0] = highs.iloc[0] - lows.iloc[0]
    for t in range(1, n):
        tr[t] = max(
            highs.iloc[t] - lows.iloc[t],
            abs(highs.iloc[t] - closes.iloc[t - 1]),
            abs(lows.iloc[t] - closes.iloc[t - 1]),
        )

    out = [float("nan")] * n
    if n < period:
        return pd.Series(out, index=highs.index)

    seed = sum(tr[0:period]) / period
    out[period - 1] = seed
    prev = seed
    for t in range(period, n):
        prev = (prev * (period - 1) + tr[t]) / period
        out[t] = prev
    return pd.Series(out, index=highs.index)


def stochastic_oscillator(
    highs: pd.Series, lows: pd.Series, closes: pd.Series, k_period: int = 14, d_period: int = 3
) -> Tuple[pd.Series, pd.Series]:
    """Stochastic Oscillator (Lane). %K measures where the current close
    sits within the trailing k_period high/low range; %D is a simple
    moving average of %K used as its signal line.

        %K[t] = 100 * (close[t] - LL[t]) / (HH[t] - LL[t])
        LL[t] = min(low[t-k_period+1 .. t]);  HH[t] = max(high[...])
        %D[t] = SMA(%K, d_period)[t]

    If HH == LL (a completely flat window — no range at all), %K is
    defined as 50.0 (neither overbought nor oversold), not a
    division-by-zero crash.
    """
    hh = highs.rolling(window=k_period, min_periods=k_period).max()
    ll = lows.rolling(window=k_period, min_periods=k_period).min()
    rng = hh - ll
    pct_k = pd.Series([float("nan")] * len(closes), index=closes.index)
    for i in range(len(closes)):
        if pd.isna(rng.iloc[i]):
            continue
        if rng.iloc[i] == 0:
            pct_k.iloc[i] = 50.0
        else:
            pct_k.iloc[i] = 100.0 * (closes.iloc[i] - ll.iloc[i]) / rng.iloc[i]
    pct_d = sma(pct_k, d_period)
    return pct_k, pct_d


def compute_all(df: pd.DataFrame, *, atr_period: int = 14, rsi_period: int = 14,
                 bb_period: int = 20, bb_num_std: float = 2.0,
                 sma_trend_period: int = 50) -> pd.DataFrame:
    """Convenience wrapper: given a DataFrame with columns
    open/high/low/close/volume, returns a NEW DataFrame (input not
    mutated) with every indicator this phase's signal engine uses
    appended as columns. signals.py is the only caller that needs to
    know all these column names at once."""
    out = df.copy()
    out["sma_trend"] = sma(df["close"], sma_trend_period)
    out["rsi"] = rsi(df["close"], rsi_period)
    macd_line, signal_line, hist = macd(df["close"])
    out["macd_line"] = macd_line
    out["macd_signal"] = signal_line
    out["macd_hist"] = hist
    bb_mid, bb_upper, bb_lower = bollinger_bands(df["close"], bb_period, bb_num_std)
    out["bb_mid"] = bb_mid
    out["bb_upper"] = bb_upper
    out["bb_lower"] = bb_lower
    out["atr"] = atr(df["high"], df["low"], df["close"], atr_period)
    pct_k, pct_d = stochastic_oscillator(df["high"], df["low"], df["close"])
    out["stoch_k"] = pct_k
    out["stoch_d"] = pct_d
    return out


def last_valid(series: pd.Series) -> float:
    """NaN-safe 'give me the last value as a plain float or nan' helper —
    used throughout signals.py/backtester.py to avoid numpy scalar types
    leaking into ledger payloads (which must be plain-JSON-serializable)."""
    if len(series) == 0:
        return float("nan")
    v = series.iloc[-1]
    return float(v) if not pd.isna(v) else float("nan")
