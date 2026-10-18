"""
Performance metrics for F&O backtests — Sharpe/Sortino/Calmar/drawdown/profit factor/expectancy.

Deliberately reuses trading_risk_engine/performance.py's own primitives (same formulas, same conventions)
instead of re-deriving them: those functions only need an equity-value series and objects with
`.pnl` / `.r_multiple`, which FOTrade and FOEquityPoint already provide. Nothing here knows about
lots or margin — equity already nets those out.

Two JSON-safety rules, both because these dicts cross the MCP wire:
  * profit_factor is capped at PROFIT_FACTOR_CAP when there are no losing trades (the equity
    report uses float('inf'), which is not valid JSON).
  * every value is a plain Python float/int, never numpy.
"""
from __future__ import annotations

from typing import Any, Dict, Sequence

import pandas as pd

from .. import performance as perf

PROFIT_FACTOR_CAP = 999.0


def build_fo_performance(equity_curve: Sequence[Any], trades: Sequence[Any], *,
                         annual_risk_free_pct: float, periods_per_year: int = 252) -> Dict[str, Any]:
    if not equity_curve:
        raise ValueError("cannot build a performance report from an empty equity curve")
    values = [float(p.equity) for p in equity_curve]
    start, end = values[0], values[-1]
    returns = pd.Series(values).pct_change().dropna()

    max_dd, dd_days = perf.max_drawdown_and_duration(values)
    total_return_pct = ((end / start) - 1.0) * 100.0 if start > 0 else 0.0
    cagr_pct = perf.cagr(start, end, len(values)) * 100.0
    stats = perf.trade_stats(list(trades))
    kelly = perf.kelly_fraction(stats.win_rate_pct, stats.avg_win, stats.avg_loss)
    pf = stats.profit_factor
    pf = PROFIT_FACTOR_CAP if pf == float("inf") else min(float(pf), PROFIT_FACTOR_CAP)

    return {
        "total_return_pct": round(float(total_return_pct), 4),
        "cagr_pct": round(float(cagr_pct), 4),
        "sharpe_ratio": round(float(perf.annualized_sharpe(returns, annual_risk_free_pct, periods_per_year)), 4),
        "sortino_ratio": round(float(perf.annualized_sortino(returns, annual_risk_free_pct, periods_per_year)), 4),
        "calmar_ratio": round(float(perf.calmar_ratio(cagr_pct, max_dd * 100.0)), 4),
        "max_drawdown_pct": round(float(max_dd * 100.0), 4),
        "max_drawdown_duration_days": int(dd_days),
        "profit_factor": round(pf, 4),
        "expectancy_r": round(float(stats.expectancy_r), 4),
        "kelly_fraction": (round(float(kelly), 4) if kelly is not None else None),
        "num_days": len(values),
    }
