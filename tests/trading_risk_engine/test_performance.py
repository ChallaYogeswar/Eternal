import math

import numpy as np
import pandas as pd
import pytest

from trading_risk_engine import performance as perf
from trading_risk_engine.models import EquityPoint, ExitReason, Trade


# ---------------------------------------------------------------------------
# The loss-recovery asymmetry — Eternal_Master_Build_Plan.md Section 7.2's
# own cited figures, made executable and tested against those exact numbers.
# ---------------------------------------------------------------------------

def test_recovery_return_needed_matches_build_plan_figures():
    # "Clawing back from a 20-30% drawdown in seven days requires a
    # 25-43% weekly gain" — the exact pair of figures cited.
    assert perf.recovery_return_needed(0.20) == pytest.approx(0.25, rel=1e-9)
    assert perf.recovery_return_needed(0.30) == pytest.approx(0.428571, rel=1e-5)


def test_recovery_return_needed_symmetric_small_loss():
    # A small loss needs an almost-equal (but strictly larger) gain.
    assert perf.recovery_return_needed(0.01) == pytest.approx(1.0 / 99.0, rel=1e-9)


def test_recovery_return_needed_rejects_total_loss():
    with pytest.raises(ValueError):
        perf.recovery_return_needed(1.0)
    with pytest.raises(ValueError):
        perf.recovery_return_needed(1.5)


def test_recovery_return_needed_rejects_negative_input():
    with pytest.raises(ValueError):
        perf.recovery_return_needed(-0.1)


def test_is_recovery_target_unrealistic():
    # 25% "recovery" spread over just 7 days is exactly the build plan's
    # own bad example -> unrealistic under the default (much lower) ceiling.
    assert perf.is_recovery_target_unrealistic(25.0, 7) is True
    # 25% spread over a full year is a perfectly ordinary annual target.
    assert perf.is_recovery_target_unrealistic(25.0, 365) is False


# ---------------------------------------------------------------------------
# Max drawdown + duration — hand-derived, including the case where the
# deepest drawdown and the longest-duration drawdown are different episodes.
# ---------------------------------------------------------------------------

def test_max_drawdown_and_duration_hand_calculation():
    # equity = [100, 110, 105, 90, 95, 120, 80]
    # peaks:    100  110  110  110  110  120  120
    # dd:         0    0 -.0455 -.1818 -.1364   0 -.3333
    # duration:   0    0    1      2      3     0    1
    # -> deepest drawdown (-33.33%) happens at the END (duration=1, ongoing);
    #    longest duration (3 bars) happens in the FIRST, already-recovered
    #    episode. These must be reported independently, not conflated.
    equity = [100.0, 110.0, 105.0, 90.0, 95.0, 120.0, 80.0]
    max_dd, max_duration = perf.max_drawdown_and_duration(equity)
    assert max_dd == pytest.approx(-0.333333, rel=1e-5)
    assert max_duration == 3


def test_max_drawdown_empty_series():
    assert perf.max_drawdown_and_duration([]) == (0.0, 0)


def test_max_drawdown_monotonically_increasing_is_zero():
    equity = [100.0, 105.0, 110.0, 120.0]
    max_dd, max_duration = perf.max_drawdown_and_duration(equity)
    assert max_dd == pytest.approx(0.0)
    assert max_duration == 0


# ---------------------------------------------------------------------------
# CAGR — hand-derived
# ---------------------------------------------------------------------------

def test_cagr_one_year_doubling():
    assert perf.cagr(100.0, 200.0, 365.25) == pytest.approx(1.0, rel=1e-6)


def test_cagr_two_years():
    # (1.5)^(365/730) = sqrt(1.5) = 1.224745 -> CAGR = 22.4745%
    result = perf.cagr(100.0, 150.0, 730.5)
    assert result == pytest.approx(math.sqrt(1.5) - 1.0, rel=1e-4)


def test_cagr_degenerate_inputs_return_zero_not_crash():
    assert perf.cagr(0.0, 100.0, 365) == 0.0
    assert perf.cagr(100.0, 200.0, 0) == 0.0
    assert perf.cagr(100.0, 200.0, -5) == 0.0


# ---------------------------------------------------------------------------
# Sharpe / Sortino — cross-checked against an independent numpy/pandas
# formula written directly in the test, not against this module's own code.
# ---------------------------------------------------------------------------

def test_sharpe_matches_independent_reference_calculation():
    equity = pd.Series([100.0, 102.0, 101.0, 105.0, 103.0, 108.0])
    returns = equity.pct_change().dropna()
    rf_annual = 0.06
    periods = 252
    expected = (returns.mean() - rf_annual / periods) / returns.std(ddof=1) * math.sqrt(periods)
    actual = perf.annualized_sharpe(returns, rf_annual, periods)
    assert actual == pytest.approx(expected, rel=1e-9)


def test_sharpe_zero_variance_returns_zero_not_inf():
    returns = pd.Series([0.01, 0.01, 0.01, 0.01])
    assert perf.annualized_sharpe(returns, 0.06) == 0.0


def test_sharpe_insufficient_data_returns_zero():
    assert perf.annualized_sharpe(pd.Series([0.01]), 0.06) == 0.0
    assert perf.annualized_sharpe(pd.Series([], dtype=float), 0.06) == 0.0


def test_sortino_only_penalizes_downside():
    # Two series with identical mean and identical downside moves, but one
    # has a much bigger upside outlier — Sortino must be IDENTICAL between
    # them (only downside deviation matters), while Sharpe would differ.
    rf = 0.0
    returns_a = pd.Series([0.01, -0.02, 0.01, -0.02, 0.01, -0.02])
    returns_b = pd.Series([0.01, -0.02, 0.50, -0.02, 0.01, -0.02])  # one big up-day added in spirit
    sortino_a = perf.annualized_sortino(returns_a, rf)
    sortino_b = perf.annualized_sortino(returns_b, rf)
    sharpe_a = perf.annualized_sharpe(returns_a, rf)
    sharpe_b = perf.annualized_sharpe(returns_b, rf)
    # Sortino's downside-only denominator is unaffected by the upside outlier
    # in b except through the (small) shift in mean; Sharpe's denominator
    # (total volatility) is affected a lot more by the same outlier.
    assert sortino_b > sortino_a  # more upside without more downside risk -> better Sortino
    # The Sharpe improvement from the same outlier is proportionally much
    # smaller relative to Sortino's, because Sharpe's denominator also grew.
    sharpe_gain = sharpe_b - sharpe_a
    sortino_gain = sortino_b - sortino_a
    assert sortino_gain > sharpe_gain


def test_calmar_ratio():
    assert perf.calmar_ratio(20.0, -10.0) == pytest.approx(2.0)
    assert perf.calmar_ratio(20.0, 0.0) == 0.0  # no drawdown recorded -> defined as 0, not inf


# ---------------------------------------------------------------------------
# Trade statistics — fully hand-derived
# ---------------------------------------------------------------------------

def _trade(pnl: float, risk_amount: float = 50.0) -> Trade:
    return Trade(
        symbol="TEST", entry_date="2026-01-01", exit_date="2026-01-05",
        entry_price=100.0, exit_price=100.0 + pnl, quantity=1,
        exit_reason=ExitReason.SIGNAL_EXIT, commission_paid=0.0,
        pnl=pnl, pnl_pct=pnl / 100.0, risk_amount=risk_amount,
    )


def test_trade_stats_hand_calculation():
    # pnls: +100, +200, -50, -100, +50, all risk_amount=50
    # wins: 100,200,50 (3); losses: 50,100 (2)
    # win_rate = 3/5 = 60%
    # avg_win = 350/3 = 116.6667; avg_loss = 150/2 = 75.0
    # profit_factor = 350/150 = 2.333333
    # R-multiples: 2, 4, -1, -2, 1 -> mean = 4/5 = 0.8
    trades = [_trade(100), _trade(200), _trade(-50), _trade(-100), _trade(50)]
    stats = perf.trade_stats(trades)
    assert stats.num_trades == 5
    assert stats.num_winning_trades == 3
    assert stats.num_losing_trades == 2
    assert stats.win_rate_pct == pytest.approx(60.0)
    assert stats.avg_win == pytest.approx(116.666667, rel=1e-6)
    assert stats.avg_loss == pytest.approx(75.0)
    assert stats.profit_factor == pytest.approx(2.333333, rel=1e-6)
    assert stats.expectancy_r == pytest.approx(0.8, rel=1e-6)


def test_trade_stats_empty():
    stats = perf.trade_stats([])
    assert stats.num_trades == 0
    assert stats.win_rate_pct == 0.0
    assert stats.profit_factor == 0.0


def test_trade_stats_no_losses_profit_factor_is_inf():
    trades = [_trade(100), _trade(50)]
    stats = perf.trade_stats(trades)
    assert stats.profit_factor == float("inf")


def test_r_multiple_zero_risk_is_safe():
    t = _trade(100, risk_amount=0.0)
    assert t.r_multiple == 0.0  # would otherwise divide by zero


# ---------------------------------------------------------------------------
# Kelly fraction — informational only, hand-derived
# ---------------------------------------------------------------------------

def test_kelly_fraction_hand_calculation():
    # W=0.6, avg_win=116.6667, avg_loss=75.0 -> R=1.555556
    # f* = 0.6 - 0.4/1.555556 = 0.342857
    f = perf.kelly_fraction(60.0, 116.666667, 75.0)
    assert f == pytest.approx(0.342857, rel=1e-5)


def test_kelly_fraction_no_edge_floors_at_zero():
    # W=0.3, avg_win=avg_loss=100 -> R=1.0 -> f*=0.3-0.7/1.0=-0.4 -> floored to 0
    f = perf.kelly_fraction(30.0, 100.0, 100.0)
    assert f == 0.0


def test_kelly_fraction_undefined_without_losses():
    assert perf.kelly_fraction(100.0, 50.0, 0.0) is None


# ---------------------------------------------------------------------------
# End-to-end report assembly
# ---------------------------------------------------------------------------

def _equity_curve(values):
    return [EquityPoint(date=f"2026-01-{i+1:02d}", equity=v, cash=v, positions_value=0.0, drawdown_pct=0.0)
            for i, v in enumerate(values)]


def test_build_performance_report_end_to_end():
    equity = _equity_curve([100_000, 102_000, 101_000, 105_000, 103_000, 108_000, 95_000, 99_000])
    trades = [_trade(2000, 1000), _trade(-1300, 1000), _trade(4000, 1000)]
    report = perf.build_performance_report(equity, trades, annual_risk_free_pct=0.065)
    assert report.starting_equity == 100_000
    assert report.ending_equity == 99_000
    assert report.num_days == 8
    assert report.num_trades == 3
    assert report.max_drawdown_pct < 0
    assert isinstance(report.sharpe_ratio, float)
    assert isinstance(report.kelly_fraction, float)


def test_build_performance_report_is_json_serializable_even_with_numpy_inputs():
    """Regression test for a real bug found via scripts/test_mcp_e2e.py:
    numpy.float64 values silently pass every in-process check (arithmetic,
    comparisons, even pytest.approx) but are NOT JSON-serializable — a
    dataclass field holding one looks completely fine until it crosses an
    actual wire protocol (MCP, HTTP), where it breaks. This test builds
    the report from numpy-typed inputs on purpose (mirroring what a
    pandas .iloc[] lookup actually returns) and asserts the OUTPUT is
    plain-Python and genuinely round-trips through json.dumps — the same
    check a real MCP server call quietly needs to pass."""
    import json
    from dataclasses import asdict

    numpy_equity = [
        EquityPoint(date=f"2026-01-{i+1:02d}", equity=np.float64(v), cash=np.float64(v),
                   positions_value=np.float64(0.0), drawdown_pct=np.float64(0.0))
        for i, v in enumerate([100_000.0, 102_000.0, 99_000.0, 101_000.0])
    ]
    numpy_trades = [Trade(
        symbol="TEST", entry_date="2026-01-01", exit_date="2026-01-02",
        entry_price=np.float64(100.0), exit_price=np.float64(105.0), quantity=10,
        exit_reason=ExitReason.SIGNAL_EXIT, commission_paid=np.float64(5.0),
        pnl=np.float64(45.0), pnl_pct=np.float64(5.0), risk_amount=np.float64(50.0),
    )]
    report = perf.build_performance_report(numpy_equity, numpy_trades, annual_risk_free_pct=0.065)

    report_dict = asdict(report)
    json.dumps(report_dict)  # must not raise TypeError: Object of type float64 is not JSON serializable

    assert type(report.sharpe_ratio) is float
    assert type(report.starting_equity) is float
    assert type(report.num_trades) is int
    assert type(report.max_drawdown_duration_days) is int


def test_build_performance_report_rejects_empty_equity_curve():
    with pytest.raises(ValueError):
        perf.build_performance_report([], [], annual_risk_free_pct=0.065)
