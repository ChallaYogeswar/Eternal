"""
Signal engine.

Eternal_Master_Build_Plan.md Section 7.2: "Signals as explicit, auditable
rules, not a black box; an ML layer can sit on top later as a probability
score, framed as an edge, not a prediction."

Design consequence of that sentence, taken literally: every rule below is
a small, independently-readable function that inspects indicator values
and returns EITHER nothing (it has no opinion — usually because an
indicator is still in its warm-up window) or a (contribution, reason)
pair where `contribution` is a number in [-1, +1] and `reason` is a
plain-English sentence that could be read aloud and would make sense on
its own. `SignalEngine.generate` is a weighted average across whichever
rules had an opinion on this bar — never a black-box weight matrix, never
a fitted model, nothing that would need to be "explained" after the fact
rather than being self-explanatory in the ledger entry it's about to be
written into.

Rules deliberately look at TRANSITIONS (a crossover happening on THIS
bar), not levels that have been true for days, where that's the more
standard reading of the indicator (MACD, Stochastic) — a rule that fires
every single day the MACD happens to sit above its signal line would
generate a new "signal" on day 47 of an already-open, already-correctly-
sized position, which is noise, not information.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import pandas as pd

from .config import Config
from .models import Signal, SignalDirection

# A rule receives the full indicator-augmented DataFrame UP TO AND
# INCLUDING the decision bar (never further — see backtester.py, which is
# the only thing that ever slices this DataFrame, always ending exactly
# at "today"). Returns None to abstain (usually: insufficient warm-up
# data), or (contribution in [-1, 1], human-readable reason or None).
RuleFn = Callable[[pd.DataFrame, Config], Optional[Tuple[float, Optional[str]]]]


@dataclass
class SignalRule:
    name: str
    weight: float
    fn: RuleFn


def _safe_last_two(df: pd.DataFrame, col: str) -> Optional[Tuple[float, float]]:
    if col not in df.columns or len(df) < 2:
        return None
    prev, curr = df[col].iloc[-2], df[col].iloc[-1]
    if pd.isna(prev) or pd.isna(curr):
        return None
    return float(prev), float(curr)


def trend_filter_rule(df: pd.DataFrame, config: Config) -> Optional[Tuple[float, Optional[str]]]:
    """Above the long-period SMA -> a standing bullish tailwind for any
    BUY signal; below it -> a standing bearish headwind. This rule alone
    never generates a signal strong enough to cross the buy/sell
    threshold (see its weight vs. the threshold in the default
    configuration) — it exists to bias the other rules toward "trade with
    the trend," not to be a signal on its own."""
    if "sma_trend" not in df.columns or len(df) < 1:
        return None
    close = df["close"].iloc[-1]
    trend = df["sma_trend"].iloc[-1]
    if pd.isna(trend):
        return None
    if close > trend:
        return 1.0, f"price {close:.2f} above the {config.sma_trend_period}-day trend filter ({trend:.2f})"
    if close < trend:
        return -1.0, f"price {close:.2f} below the {config.sma_trend_period}-day trend filter ({trend:.2f})"
    return 0.0, None


def rsi_rule(df: pd.DataFrame, config: Config) -> Optional[Tuple[float, Optional[str]]]:
    """Discrete, not scaled — deliberately. A continuously-scaled
    "RSI-implies-a-fractional-vote" rule would be harder to read out loud
    from the ledger than "RSI is 24.1, which is oversold." """
    if "rsi" not in df.columns or len(df) < 1:
        return None
    value = df["rsi"].iloc[-1]
    if pd.isna(value):
        return None
    if value <= config.rsi_oversold:
        return 1.0, f"RSI {value:.1f} at/below the oversold line ({config.rsi_oversold:.0f})"
    if value >= config.rsi_overbought:
        return -1.0, f"RSI {value:.1f} at/above the overbought line ({config.rsi_overbought:.0f})"
    return 0.0, None


def macd_crossover_rule(df: pd.DataFrame, config: Config) -> Optional[Tuple[float, Optional[str]]]:
    """Fires only on the bar the histogram actually CROSSES zero — a
    bullish (or bearish) crossover is a single event, not a standing
    condition, and re-voting on it every day it happens to still be true
    would double-count the same piece of evidence for as long as the
    trend persists."""
    hist = _safe_last_two(df, "macd_hist")
    if hist is None:
        return None
    prev_hist, curr_hist = hist
    if prev_hist <= 0 < curr_hist:
        return 1.0, "MACD histogram just crossed above zero (bullish crossover)"
    if prev_hist >= 0 > curr_hist:
        return -1.0, "MACD histogram just crossed below zero (bearish crossover)"
    return 0.0, None


def bollinger_band_rule(df: pd.DataFrame, config: Config) -> Optional[Tuple[float, Optional[str]]]:
    """Mean-reversion framing: price outside its own recent volatility
    band is read as stretched, not as a breakout to chase — consistent
    with this being a secondary, confirming rule (lower default weight)
    rather than a primary trend-following signal, which would want the
    opposite interpretation of the same event."""
    if "bb_upper" not in df.columns or "bb_lower" not in df.columns or len(df) < 1:
        return None
    close = df["close"].iloc[-1]
    upper, lower = df["bb_upper"].iloc[-1], df["bb_lower"].iloc[-1]
    if pd.isna(upper) or pd.isna(lower):
        return None
    if close < lower:
        return 1.0, f"close {close:.2f} below the lower Bollinger Band ({lower:.2f}) — stretched to the downside"
    if close > upper:
        return -1.0, f"close {close:.2f} above the upper Bollinger Band ({upper:.2f}) — stretched to the upside"
    return 0.0, None


def stochastic_crossover_rule(df: pd.DataFrame, config: Config) -> Optional[Tuple[float, Optional[str]]]:
    """%K crossing %D is only treated as meaningful inside the oversold
    or overbought zones — the same crossover in the middle of the range
    happens constantly and carries little information on its own."""
    k = _safe_last_two(df, "stoch_k")
    d = _safe_last_two(df, "stoch_d")
    if k is None or d is None:
        return None
    prev_k, curr_k = k
    prev_d, curr_d = d
    crossed_up = prev_k <= prev_d and curr_k > curr_d
    crossed_down = prev_k >= prev_d and curr_k < curr_d
    if crossed_up and curr_k < config.stoch_oversold:
        return 1.0, f"%K crossed above %D inside the oversold zone (%K={curr_k:.1f})"
    if crossed_down and curr_k > config.stoch_overbought:
        return -1.0, f"%K crossed below %D inside the overbought zone (%K={curr_k:.1f})"
    return 0.0, None


DEFAULT_RULES: List[SignalRule] = [
    SignalRule("trend_filter", 1.0, trend_filter_rule),
    SignalRule("rsi", 1.0, rsi_rule),
    SignalRule("macd_crossover", 1.0, macd_crossover_rule),
    SignalRule("bollinger_band", 0.75, bollinger_band_rule),
    SignalRule("stochastic_crossover", 0.75, stochastic_crossover_rule),
]


class SignalEngine:
    def __init__(self, config: Config, rules: Optional[List[SignalRule]] = None):
        self.config = config
        self.rules = rules if rules is not None else DEFAULT_RULES

    def generate(self, symbol: str, indicator_df: pd.DataFrame) -> Signal:
        """`indicator_df` must already have every indicator column this
        engine's rules reference (see indicators.compute_all) and must
        end exactly at the bar the decision is being made for — this
        function trusts that boundary completely and never looks past
        the last row itself."""
        if len(indicator_df) == 0:
            return Signal(symbol=symbol, date="", direction=SignalDirection.HOLD, score=0.0,
                          reasons=["no data"], indicator_snapshot={})

        total_weighted_score = 0.0
        total_weight = 0.0
        reasons: List[str] = []

        for rule in self.rules:
            result = rule.fn(indicator_df, self.config)
            if result is None:
                continue  # abstained — usually insufficient warm-up data; excluded from the average entirely
            contribution, reason = result
            total_weighted_score += contribution * rule.weight
            total_weight += rule.weight
            if reason:
                reasons.append(f"[{rule.name}] {reason}")

        score = (total_weighted_score / total_weight) if total_weight > 0 else 0.0

        if total_weight == 0:
            direction = SignalDirection.HOLD
            reasons = ["insufficient indicator history for any rule to evaluate yet"]
        elif score >= self.config.signal_buy_threshold:
            direction = SignalDirection.BUY
        elif score <= self.config.signal_sell_threshold:
            direction = SignalDirection.SELL
        else:
            direction = SignalDirection.HOLD
            if not reasons:
                reasons = [f"combined score {score:.3f} inside the hold band "
                           f"[{self.config.signal_sell_threshold}, {self.config.signal_buy_threshold}]"]

        last_row = indicator_df.iloc[-1]
        snapshot = {
            col: (float(last_row[col]) if pd.notna(last_row[col]) else None)
            for col in indicator_df.columns
            if col not in ("open", "high", "low", "close", "volume")
        }
        date_value = indicator_df.index[-1]
        date_str = date_value.isoformat() if hasattr(date_value, "isoformat") else str(date_value)

        return Signal(symbol=symbol, date=date_str, direction=direction, score=round(score, 6),
                      reasons=reasons, indicator_snapshot=snapshot)
