"""
Go-live gate for Sub-phase B — the Master Build Plan's Phase 6 exit criterion, as code.

    "Exit criteria for going live: sub-phase A shows a real edge across enough backtested and
     paper-traded trades to be statistically meaningful — not a good week."

evaluate_go_live() turns that sentence into explicit, individually-reported checks. It is
ADVISORY and fail-closed: it never enables live trading (PHASE6_LIVE_TRADING_ENABLED stays a
human decision), it only answers "does the evidence clear the bar you wrote down", and any
missing/insufficient evidence is a FAIL, never a pass-by-default.

Inputs are plain lists of per-trade R-multiples (pnl / risk_amount — the unit both the equity
and F&O backtesters and the paper trader already report), so it works for equity, F&O, or both.

Statistics: one-sided test that mean R > 0 using a t-statistic and a normal-approximation
p-value (math.erfc; reasonable for n >= 30, which min_*_trades enforces — this is NOT a
substitute for out-of-sample thinking: backtests on simulated data prove the plumbing, not an edge).
Thresholds are conservative defaults, overridable via GoLiveCriteria.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence


@dataclass(frozen=True)
class GoLiveCriteria:
    min_backtest_trades: int = 100
    min_paper_trades: int = 30
    min_paper_trading_days: int = 28        # "several weeks minimum" — 4 weeks
    max_p_value: float = 0.05               # one-sided, mean R > 0, on backtest+paper combined
    min_profit_factor: float = 1.2
    max_drawdown_pct: float = 20.0          # percent of equity
    require_paper_mean_r_positive: bool = True   # paper must agree in sign with the backtest


@dataclass
class GoLiveCheck:
    name: str
    passed: bool
    detail: str


@dataclass
class GoLiveReport:
    ready: bool
    checks: List[GoLiveCheck] = field(default_factory=list)
    mean_r: Optional[float] = None
    t_stat: Optional[float] = None
    p_value: Optional[float] = None
    note: str = ("Advisory only — this never enables live trading. Simulated market data demonstrates "
                 "plumbing, not a real edge; run sub-phase A on real data before relying on a pass.")


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def t_test_mean_positive(r_multiples: Sequence[float]):
    """Returns (mean, t_stat, one_sided_p). p=1.0 / t=0.0 when undefined (n<2 or zero variance with
    non-positive mean); zero variance with a positive mean is treated as p=0 only if n>=2."""
    n = len(r_multiples)
    if n < 2:
        return (_mean(r_multiples) if n else 0.0), 0.0, 1.0
    m = _mean(r_multiples)
    var = sum((x - m) ** 2 for x in r_multiples) / (n - 1)
    if var == 0:
        return m, (math.inf if m > 0 else 0.0), (0.0 if m > 0 else 1.0)
    t = m / math.sqrt(var / n)
    p = 0.5 * math.erfc(t / math.sqrt(2))   # one-sided, normal approximation
    return m, t, p


def _max_drawdown_pct(equity_values: Sequence[float]) -> Optional[float]:
    if not equity_values:
        return None
    peak, worst = equity_values[0], 0.0
    for v in equity_values:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst * 100.0


def _profit_factor(r: Sequence[float]) -> float:
    gp = sum(x for x in r if x > 0)
    gl = sum(-x for x in r if x <= 0)
    return math.inf if gl == 0 and gp > 0 else (gp / gl if gl > 0 else 0.0)


def evaluate_go_live(
    backtest_r_multiples: Sequence[float],
    paper_r_multiples: Sequence[float],
    paper_trading_days: int,
    equity_values: Optional[Sequence[float]] = None,
    criteria: GoLiveCriteria = GoLiveCriteria(),
) -> GoLiveReport:
    bt, pp = list(backtest_r_multiples), list(paper_r_multiples)
    combined = bt + pp
    checks: List[GoLiveCheck] = []

    def add(name, passed, detail):
        checks.append(GoLiveCheck(name, bool(passed), detail))

    add("backtest_sample_size", len(bt) >= criteria.min_backtest_trades,
        f"{len(bt)} backtest trades (need >= {criteria.min_backtest_trades})")
    add("paper_sample_size", len(pp) >= criteria.min_paper_trades,
        f"{len(pp)} paper trades (need >= {criteria.min_paper_trades})")
    add("paper_duration", paper_trading_days >= criteria.min_paper_trading_days,
        f"{paper_trading_days} days of paper trading (need >= {criteria.min_paper_trading_days})")

    mean_r, t, p = t_test_mean_positive(combined)
    enough = len(combined) >= 2
    add("statistical_edge", enough and mean_r > 0 and p <= criteria.max_p_value,
        f"mean R={mean_r:.3f}, t={t:.2f}, one-sided p={p:.4f} over {len(combined)} trades "
        f"(need mean>0 and p<={criteria.max_p_value})")

    if criteria.require_paper_mean_r_positive:
        pm = _mean(pp) if pp else 0.0
        add("paper_agrees_with_backtest", bool(pp) and pm > 0, f"paper mean R={pm:.3f} (must be > 0)")

    pf = _profit_factor(combined) if combined else 0.0
    add("profit_factor", pf >= criteria.min_profit_factor,
        f"profit factor {'inf' if pf == math.inf else f'{pf:.2f}'} (need >= {criteria.min_profit_factor})")

    dd = _max_drawdown_pct(equity_values) if equity_values else None
    add("max_drawdown", dd is not None and dd <= criteria.max_drawdown_pct,
        "no equity curve supplied — fail-closed" if dd is None
        else f"max drawdown {dd:.2f}% (limit {criteria.max_drawdown_pct}%)")

    return GoLiveReport(ready=all(c.passed for c in checks), checks=checks,
                        mean_r=mean_r, t_stat=(None if math.isinf(t) else t), p_value=p)
