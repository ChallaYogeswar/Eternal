"""
Performance metrics and the loss-recovery math.

Eternal_Master_Build_Plan.md Section 7.2, "The math correction": "any
design goal of 'recover a loss within a fixed week no matter what'
doesn't run on math, it runs against it. Clawing back from a 20-30%
drawdown in seven days requires a 25-43% weekly gain." That specific
claim is not just narrative in this phase — `recovery_return_needed()`
below is the executable form of it, unit-tested against the exact 25%/
43% figures the build plan cites, and `risk.py`'s circuit breaker imports
it directly to refuse configuration that implies an unrealistic recovery
target, rather than merely warning about one in a README.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .models import EquityPoint, ExitReason, PerformanceReport, Trade


def recovery_return_needed(drawdown_fraction: float) -> float:
    """The asymmetry of percentage losses and gains, made explicit and
    testable. Losing a fraction L of capital requires a subsequent gain
    of L/(1-L) — NOT L — to return to break-even, because the gain is
    computed on a smaller base than the loss was.

        (1 - L) * (1 + G) = 1   =>   G = L / (1 - L)

    L=0.20 -> G=0.25 (25%); L=0.30 -> G=0.428571 (~43%) — the exact pair
    of figures Eternal_Master_Build_Plan.md Section 7.2 cites. Raises
    ValueError for L>=1.0 (total loss of capital has no finite recovery
    return) rather than returning inf or a negative number silently.
    """
    if drawdown_fraction < 0:
        raise ValueError("drawdown_fraction must be >= 0 (pass magnitude, e.g. 0.20 for a 20% loss)")
    if drawdown_fraction >= 1.0:
        raise ValueError("a drawdown of 100% or more has no finite recovery return")
    return drawdown_fraction / (1.0 - drawdown_fraction)


def max_drawdown_and_duration(equity_values: Sequence[float]) -> Tuple[float, int]:
    """Returns (max_drawdown_pct, max_drawdown_duration_bars).

    max_drawdown_pct is negative-or-zero (e.g. -0.3333 for a 33.33% peak-
    to-trough decline). max_drawdown_duration_bars is the longest number
    of bars spent between a peak and the bar a NEW peak is subsequently
    reached — for a drawdown still open at the end of the series, the
    last bar stands in for "now." These two numbers can legitimately come
    from DIFFERENT drawdown episodes: the deepest decline and the longest
    time-underwater stretch are different questions with different
    answers, and this function does not conflate them (see
    tests/test_performance.py for a worked example where they diverge).
    """
    if len(equity_values) == 0:
        return 0.0, 0
    peak = equity_values[0]
    peak_idx = 0
    max_dd = 0.0
    max_duration = 0
    for i, v in enumerate(equity_values):
        if v > peak:
            peak = v
            peak_idx = i
        dd = (v - peak) / peak if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
        duration = i - peak_idx
        if duration > max_duration:
            max_duration = duration
    return max_dd, max_duration


def cagr(starting_equity: float, ending_equity: float, num_days: float) -> float:
    """Compound Annual Growth Rate, annualized on ACTUAL elapsed calendar
    days (not trading days — CAGR describes real time, unlike Sharpe's
    trading-day-based annualization below). Returns 0.0 for a zero- or
    negative-day span or non-positive starting equity rather than raising
    or returning inf/NaN — a backtest with insufficient data should report
    a boring number, not crash the report."""
    if num_days <= 0 or starting_equity <= 0:
        return 0.0
    years = num_days / 365.25
    if years <= 0:
        return 0.0
    return (ending_equity / starting_equity) ** (1.0 / years) - 1.0


def annualized_sharpe(daily_returns: pd.Series, annual_risk_free_pct: float, periods_per_year: int = 252) -> float:
    """Sharpe ratio, annualized from daily returns.

        daily_rf   = annual_risk_free_pct / periods_per_year   (linear approximation,
                     not the exact geometric (1+rf)^(1/252)-1 — the difference is
                     negligible at typical risk-free rates and this linear form is
                     the common convention; documented rather than silently assumed)
        Sharpe     = (mean(daily_returns) - daily_rf) / std(daily_returns, ddof=1)
                     * sqrt(periods_per_year)

    Returns 0.0 (not inf/NaN) when there are fewer than 2 return
    observations or the return series has zero variance (e.g., a flat
    equity curve) — a meaningless ratio should read as "no signal," not
    crash the report or silently print `inf`.
    """
    r = daily_returns.dropna()
    if len(r) < 2:
        return 0.0
    std = r.std(ddof=1)
    if std == 0 or math.isnan(std):
        return 0.0
    daily_rf = annual_risk_free_pct / periods_per_year
    return float((r.mean() - daily_rf) / std * math.sqrt(periods_per_year))


def annualized_sortino(daily_returns: pd.Series, annual_risk_free_pct: float, periods_per_year: int = 252) -> float:
    """Sortino ratio — Sharpe's own formula, but the denominator only
    penalizes downside deviation (returns below the daily risk-free
    target), not ordinary upside volatility. A strategy with occasional
    large up-days and small, consistent down-days looks better here than
    under Sharpe, which is the entire point of using both metrics rather
    than either alone."""
    r = daily_returns.dropna()
    if len(r) < 2:
        return 0.0
    daily_rf = annual_risk_free_pct / periods_per_year
    downside = (r - daily_rf).clip(upper=0.0)
    downside_deviation = math.sqrt((downside ** 2).mean())
    if downside_deviation == 0 or math.isnan(downside_deviation):
        return 0.0
    return float((r.mean() - daily_rf) / downside_deviation * math.sqrt(periods_per_year))


def calmar_ratio(cagr_pct: float, max_drawdown_pct: float) -> float:
    """CAGR / |max drawdown|. Returns 0.0 when max_drawdown_pct is 0
    (no drawdown ever recorded, e.g. a too-short backtest) instead of
    dividing by zero."""
    if max_drawdown_pct == 0:
        return 0.0
    return cagr_pct / abs(max_drawdown_pct)


@dataclass
class TradeStats:
    win_rate_pct: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    expectancy_r: float
    num_trades: int
    num_winning_trades: int
    num_losing_trades: int


def trade_stats(trades: List[Trade]) -> TradeStats:
    """All figures computed directly from the trade list — no shortcuts
    through the equity curve — so this can be sanity-checked independently
    against a plain list of entry/exit prices."""
    if not trades:
        return TradeStats(0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0)

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]

    win_rate = len(wins) / len(trades)
    avg_win = (sum(t.pnl for t in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(abs(t.pnl) for t in losses) / len(losses)) if losses else 0.0

    gross_profit = sum(t.pnl for t in wins)
    gross_loss = sum(abs(t.pnl) for t in losses)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    r_multiples = [t.r_multiple for t in trades]
    expectancy_r = sum(r_multiples) / len(r_multiples)

    return TradeStats(
        win_rate_pct=win_rate * 100.0, avg_win=avg_win, avg_loss=avg_loss,
        profit_factor=profit_factor, expectancy_r=expectancy_r,
        num_trades=len(trades), num_winning_trades=len(wins), num_losing_trades=len(losses),
    )


def kelly_fraction(win_rate_pct: float, avg_win: float, avg_loss: float) -> Optional[float]:
    """Informational only — Eternal_Master_Build_Plan.md Section 7.2
    prescribes fixed-fractional position sizing explicitly, NOT Kelly
    sizing, and risk.py never calls this function when deciding a real
    position size. This exists so a backtest report can tell you "your
    edge implies a full-Kelly of X%, and this strategy deliberately risks
    far less than that per trade" — full Kelly is known to be far too
    aggressive for real capital (it assumes the estimated win rate and
    payoff ratio are exact and stationary, which they never are), so
    reporting it without using it is the responsible way to show the
    number.

        f* = W - (1-W)/R,  R = avg_win / avg_loss (payoff ratio)

    Returns None if avg_loss is 0 (no losing trades yet — R is undefined,
    not infinite) or if there are no trades at all. Returns 0.0 (not a
    division-by-zero warning) if avg_win is 0 (every trade so far has
    been a loss — the payoff ratio is well-defined at 0 in that case, and
    Kelly's own answer to "no wins yet" is unambiguously "size this at
    zero," not "undefined"). A negative result (no edge) is also
    returned as 0.0 — Kelly's interpretation of a negative fraction is
    "don't take this bet," which floors at not betting rather than
    surfacing a negative size.
    """
    if avg_loss <= 0:
        return None
    if avg_win <= 0:
        return 0.0
    w = win_rate_pct / 100.0
    r = avg_win / avg_loss
    f = w - (1.0 - w) / r
    return max(0.0, f)


def build_performance_report(
    equity_curve: List[EquityPoint],
    trades: List[Trade],
    *,
    annual_risk_free_pct: float,
    periods_per_year: int = 252,
) -> PerformanceReport:
    """The single entry point backtester.py and paper_trader.py both call
    to turn raw simulation output into the report structure the rest of
    the system (and the person reading it) actually consumes."""
    if not equity_curve:
        raise ValueError("cannot build a performance report from an empty equity curve")

    equity_values = [p.equity for p in equity_curve]
    starting_equity = equity_values[0]
    ending_equity = equity_values[-1]
    num_days = len(equity_curve)

    equity_series = pd.Series(equity_values)
    daily_returns = equity_series.pct_change().dropna()

    max_dd, max_dd_duration = max_drawdown_and_duration(equity_values)
    total_return_pct = ((ending_equity / starting_equity) - 1.0) * 100.0 if starting_equity > 0 else 0.0
    cagr_pct = cagr(starting_equity, ending_equity, num_days) * 100.0
    sharpe = annualized_sharpe(daily_returns, annual_risk_free_pct, periods_per_year)
    sortino = annualized_sortino(daily_returns, annual_risk_free_pct, periods_per_year)
    calmar = calmar_ratio(cagr_pct, max_dd * 100.0)

    stats = trade_stats(trades)
    kelly = kelly_fraction(stats.win_rate_pct, stats.avg_win, stats.avg_loss)

    return PerformanceReport(
        total_return_pct=float(total_return_pct),
        cagr_pct=float(cagr_pct),
        sharpe_ratio=float(sharpe),
        sortino_ratio=float(sortino),
        calmar_ratio=float(calmar),
        max_drawdown_pct=float(max_dd * 100.0),
        max_drawdown_duration_days=int(max_dd_duration),
        win_rate_pct=float(stats.win_rate_pct),
        profit_factor=float(stats.profit_factor),
        expectancy_r=float(stats.expectancy_r),
        avg_win=float(stats.avg_win),
        avg_loss=float(stats.avg_loss),
        num_trades=int(stats.num_trades),
        num_winning_trades=int(stats.num_winning_trades),
        num_losing_trades=int(stats.num_losing_trades),
        kelly_fraction=(float(kelly) if kelly is not None else None),
        starting_equity=float(starting_equity),
        ending_equity=float(ending_equity),
        num_days=int(num_days),
    )


def is_recovery_target_unrealistic(target_return_pct: float, period_days: int, *, sanity_ceiling_pct: float = 15.0) -> bool:
    """A standalone, tested diagnostic — NOT wired into an automatic
    config gate, because this project deliberately never introduces a
    "recovery target" configuration field in the first place (the correct
    response to Eternal_Master_Build_Plan.md Section 7.2's math correction
    is to not build that feature, not to build it and then validate it).
    This exists so the question "would recovering X% in Y days be
    realistic" always has one canonical, tested answer available —
    exposed as `trading_check_recovery_math` at the MCP layer (see
    mcp_servers/trading_engine/data.py) for exactly the moment someone
    (human or a future Claude session) is tempted to hand-configure an
    aggressive catch-up plan and should see the math first.
    `sanity_ceiling_pct` defaults well below even the LOW end of the
    build plan's own 25-43%-per-week example, deliberately: the build
    plan's point is that the whole range is already unreasonable, not
    that there's a safe number just under 25%."""
    if period_days <= 0:
        return True
    weekly_equivalent = target_return_pct * (7.0 / period_days)
    return weekly_equivalent > sanity_ceiling_pct
