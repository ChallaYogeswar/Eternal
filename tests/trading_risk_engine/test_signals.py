import math

import pandas as pd
import pytest

from trading_risk_engine.config import Config
from trading_risk_engine.models import SignalDirection
from trading_risk_engine.signals import (
    DEFAULT_RULES,
    SignalEngine,
    SignalRule,
    bollinger_band_rule,
    macd_crossover_rule,
    rsi_rule,
    stochastic_crossover_rule,
    trend_filter_rule,
)


def _cfg(**overrides) -> Config:
    defaults = dict(data_dir="/tmp/phase6_signals_test_data")
    defaults.update(overrides)
    return Config(**defaults)


def _df(rows: list) -> pd.DataFrame:
    """Builds a minimal indicator DataFrame from a list of dicts, one per
    row (in chronological order, last row = the decision bar)."""
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# trend_filter_rule
# ---------------------------------------------------------------------------

def test_trend_filter_rule_bullish():
    df = _df([{"close": 110.0, "sma_trend": 100.0}])
    result = trend_filter_rule(df, _cfg())
    assert result[0] == 1.0


def test_trend_filter_rule_bearish():
    df = _df([{"close": 90.0, "sma_trend": 100.0}])
    result = trend_filter_rule(df, _cfg())
    assert result[0] == -1.0


def test_trend_filter_rule_exactly_equal_is_neutral():
    df = _df([{"close": 100.0, "sma_trend": 100.0}])
    result = trend_filter_rule(df, _cfg())
    assert result == (0.0, None)


def test_trend_filter_rule_abstains_on_nan():
    df = _df([{"close": 100.0, "sma_trend": float("nan")}])
    assert trend_filter_rule(df, _cfg()) is None


# ---------------------------------------------------------------------------
# rsi_rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [(25.0, 1.0), (30.0, 1.0), (75.0, -1.0), (70.0, -1.0), (50.0, 0.0)])
def test_rsi_rule_thresholds(value, expected):
    df = _df([{"rsi": value}])
    result = rsi_rule(df, _cfg())
    assert result[0] == expected


def test_rsi_rule_abstains_on_nan():
    df = _df([{"rsi": float("nan")}])
    assert rsi_rule(df, _cfg()) is None


# ---------------------------------------------------------------------------
# macd_crossover_rule — only fires on the actual crossing bar
# ---------------------------------------------------------------------------

def test_macd_crossover_bullish():
    df = _df([{"macd_hist": -1.0}, {"macd_hist": 1.0}])
    result = macd_crossover_rule(df, _cfg())
    assert result[0] == 1.0


def test_macd_crossover_bearish():
    df = _df([{"macd_hist": 1.0}, {"macd_hist": -1.0}])
    result = macd_crossover_rule(df, _cfg())
    assert result[0] == -1.0


def test_macd_crossover_boundary_zero_counts_as_crossing():
    df = _df([{"macd_hist": 0.0}, {"macd_hist": 0.5}])
    result = macd_crossover_rule(df, _cfg())
    assert result[0] == 1.0


def test_macd_no_crossover_when_already_positive_is_neutral():
    df = _df([{"macd_hist": 1.0}, {"macd_hist": 2.0}])
    result = macd_crossover_rule(df, _cfg())
    assert result == (0.0, None)


def test_macd_crossover_abstains_with_only_one_row():
    df = _df([{"macd_hist": 1.0}])
    assert macd_crossover_rule(df, _cfg()) is None


def test_macd_crossover_abstains_on_nan():
    df = _df([{"macd_hist": float("nan")}, {"macd_hist": 1.0}])
    assert macd_crossover_rule(df, _cfg()) is None


# ---------------------------------------------------------------------------
# bollinger_band_rule
# ---------------------------------------------------------------------------

def test_bollinger_rule_below_lower_band():
    df = _df([{"close": 95.0, "bb_lower": 100.0, "bb_upper": 110.0}])
    assert bollinger_band_rule(df, _cfg())[0] == 1.0


def test_bollinger_rule_above_upper_band():
    df = _df([{"close": 115.0, "bb_lower": 100.0, "bb_upper": 110.0}])
    assert bollinger_band_rule(df, _cfg())[0] == -1.0


def test_bollinger_rule_inside_bands_is_neutral():
    df = _df([{"close": 105.0, "bb_lower": 100.0, "bb_upper": 110.0}])
    assert bollinger_band_rule(df, _cfg()) == (0.0, None)


# ---------------------------------------------------------------------------
# stochastic_crossover_rule
# ---------------------------------------------------------------------------

def test_stochastic_bullish_crossover_in_oversold_zone():
    df = _df([
        {"stoch_k": 15.0, "stoch_d": 17.0},
        {"stoch_k": 18.0, "stoch_d": 16.0},
    ])
    assert stochastic_crossover_rule(df, _cfg())[0] == 1.0


def test_stochastic_bearish_crossover_in_overbought_zone():
    df = _df([
        {"stoch_k": 85.0, "stoch_d": 83.0},
        {"stoch_k": 82.0, "stoch_d": 84.0},
    ])
    assert stochastic_crossover_rule(df, _cfg())[0] == -1.0


def test_stochastic_crossover_outside_zone_is_neutral():
    # Same upward crossover shape, but nowhere near the oversold zone.
    df = _df([
        {"stoch_k": 45.0, "stoch_d": 50.0},
        {"stoch_k": 55.0, "stoch_d": 48.0},
    ])
    assert stochastic_crossover_rule(df, _cfg()) == (0.0, None)


def test_stochastic_no_crossover_is_neutral():
    df = _df([
        {"stoch_k": 50.0, "stoch_d": 40.0},
        {"stoch_k": 55.0, "stoch_d": 42.0},
    ])
    assert stochastic_crossover_rule(df, _cfg()) == (0.0, None)


# ---------------------------------------------------------------------------
# SignalEngine — combined, weighted, hand-derived
# ---------------------------------------------------------------------------

def test_engine_all_rules_agree_bullish_score_is_exactly_one():
    df = _df([
        {"close": 100.0, "sma_trend": 105.0, "rsi": 50.0, "macd_hist": -1.0,
         "bb_lower": 120.0, "bb_upper": 130.0, "stoch_k": 15.0, "stoch_d": 17.0},
        {"close": 110.0, "sma_trend": 100.0, "rsi": 25.0, "macd_hist": 1.0,
         "bb_lower": 115.0, "bb_upper": 120.0, "stoch_k": 18.0, "stoch_d": 16.0},
    ])
    engine = SignalEngine(_cfg())
    signal = engine.generate("TEST.NS", df)
    assert signal.score == pytest.approx(1.0)
    assert signal.direction == SignalDirection.BUY
    assert len(signal.reasons) == 5  # every one of the 5 default rules contributed a reason


def test_engine_mixed_signals_weighted_average_hand_calculation():
    # trend: bullish (+1, w1.0); rsi: neutral (0, w1.0, no reason);
    # macd: no crossover (0, w1.0, no reason); bollinger: bearish (-1, w0.75);
    # stochastic: abstains (missing columns entirely -> excluded from weight)
    # total_weighted = 1*1.0 + 0*1.0 + 0*1.0 + (-1)*0.75 = 0.25
    # total_weight   = 1.0+1.0+1.0+0.75 = 3.75 (stochastic's 0.75 excluded)
    # score = 0.25 / 3.75 = 0.066667 -> inside [-0.35, 0.35] -> HOLD
    df = _df([
        {"close": 100.0, "sma_trend": 100.0, "rsi": 50.0, "macd_hist": 1.0,
         "bb_lower": 100.0, "bb_upper": 110.0},
        {"close": 105.0, "sma_trend": 100.0, "rsi": 50.0, "macd_hist": 2.0,
         "bb_lower": 100.0, "bb_upper": 104.0},
    ])
    engine = SignalEngine(_cfg())
    signal = engine.generate("TEST.NS", df)
    assert signal.score == pytest.approx(0.066667, rel=1e-4)
    assert signal.direction == SignalDirection.HOLD


def test_engine_total_abstention_returns_hold_with_zero_score():
    df = _df([{"close": 100.0}])  # no indicator columns at all
    engine = SignalEngine(_cfg())
    signal = engine.generate("TEST.NS", df)
    assert signal.score == 0.0
    assert signal.direction == SignalDirection.HOLD
    assert "insufficient" in signal.reasons[0]


def test_engine_empty_dataframe_is_safe():
    engine = SignalEngine(_cfg())
    signal = engine.generate("TEST.NS", pd.DataFrame())
    assert signal.direction == SignalDirection.HOLD


def test_engine_buy_threshold_boundary_is_inclusive():
    fixed_rule = SignalRule("fixed", 1.0, lambda df, cfg: (0.35, "fixed at exactly the buy threshold"))
    engine = SignalEngine(_cfg(signal_buy_threshold=0.35), rules=[fixed_rule])
    signal = engine.generate("TEST.NS", _df([{"close": 1.0}]))
    assert signal.direction == SignalDirection.BUY


def test_engine_just_below_buy_threshold_holds():
    fixed_rule = SignalRule("fixed", 1.0, lambda df, cfg: (0.349999, "just under the buy threshold"))
    engine = SignalEngine(_cfg(signal_buy_threshold=0.35), rules=[fixed_rule])
    signal = engine.generate("TEST.NS", _df([{"close": 1.0}]))
    assert signal.direction == SignalDirection.HOLD


def test_engine_sell_threshold_boundary_is_inclusive():
    fixed_rule = SignalRule("fixed", 1.0, lambda df, cfg: (-0.35, "fixed at exactly the sell threshold"))
    engine = SignalEngine(_cfg(signal_sell_threshold=-0.35), rules=[fixed_rule])
    signal = engine.generate("TEST.NS", _df([{"close": 1.0}]))
    assert signal.direction == SignalDirection.SELL


def test_engine_snapshot_excludes_raw_ohlcv_columns():
    df = _df([{"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100.0, "rsi": 55.0}])
    engine = SignalEngine(_cfg(), rules=[SignalRule("rsi", 1.0, rsi_rule)])
    signal = engine.generate("TEST.NS", df)
    assert "rsi" in signal.indicator_snapshot
    assert "close" not in signal.indicator_snapshot
    assert "volume" not in signal.indicator_snapshot


def test_default_rules_are_five_and_weighted_as_documented():
    weights = {r.name: r.weight for r in DEFAULT_RULES}
    assert weights["trend_filter"] == 1.0
    assert weights["rsi"] == 1.0
    assert weights["macd_crossover"] == 1.0
    assert weights["bollinger_band"] == 0.75
    assert weights["stochastic_crossover"] == 0.75
