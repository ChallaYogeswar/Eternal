"""
Backtesting engine.

Eternal_Master_Build_Plan.md Section 7.2: "Backtesting: backtesting.py or
vectorbt — mandatory before any real capital is involved." This module is
a purpose-built engine rather than a wrapper around either of those
libraries — see this phase's README for why — but the mandate itself
("mandatory before any real capital is involved") is the reason every
design decision below leans toward realism over favorable-looking
results:

- Decisions are made using data through bar t; fills happen at bar t+1's
  OPEN, never at a price the strategy couldn't have known when it decided
  to trade. See tests/test_backtester.py::test_no_lookahead_bias_full_
  backtest for a structural proof, not just a design claim.
- A stop-loss or take-profit that the market GAPPED THROUGH is filled at
  the worse of (the stop/target price, the next bar's open) — exactly
  what would happen to a real resting order, not the flattering
  assumption that every stop fills at exactly its nominal price.
- Commission and slippage apply to every fill, entry and exit, and are
  netted into each Trade's `pnl` — a backtest that quietly ignores these
  is the single most common way an amateur backtest overstates its own
  edge (build plan Section 7.2's own framing).
- Stop-loss and take-profit are computed once, at entry, from the ATR
  known at the moment the trade was decided, and are never recomputed or
  moved for the life of the position (Section 7.2, verbatim).
- The max-drawdown circuit breaker is checked every bar and can refuse
  new entries; it never blocks closing an existing position.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type
from typing import Dict, List, Optional

import pandas as pd

from . import indicators as ind
from . import performance as perf
from .config import Config
from .models import (
    BacktestReport,
    EquityPoint,
    ExitReason,
    Position,
    SignalDirection,
    Trade,
)
from .risk import DrawdownCircuitBreaker, RiskManager
from .signals import SignalEngine


def _to_date(value) -> date_type:
    if hasattr(value, "date"):
        return value.date()
    if isinstance(value, date_type):
        return value
    return pd.Timestamp(value).date()


def _fill_with_slippage(reference_price: float, slippage_bps: float, *, worse_for: str) -> float:
    """`worse_for` is 'buyer' or 'seller' — slippage always moves the
    fill price AGAINST whoever is filling, never in their favor, which is
    the conservative (and realistic) assumption.

    Always returns a plain Python float, even if `reference_price` is a
    numpy scalar (as every value pulled from a pandas DataFrame via
    `.iloc[...]` is) — numpy float64 is not JSON-serializable, and a
    dataclass field holding one looks fine in-process but breaks the
    moment it crosses a real wire protocol (MCP, HTTP). Caught exactly
    that way — see scripts/test_mcp_e2e.py's module docstring — which is
    why every value crosses this boundary rather than being cast ad hoc
    at dozens of call sites."""
    factor = slippage_bps / 10_000.0
    if worse_for == "buyer":
        return float(reference_price * (1.0 + factor))
    return float(reference_price * (1.0 - factor))


def _commission(price: float, quantity: int, config: Config) -> float:
    return float(config.commission_flat + config.commission_pct * price * quantity)


class Backtester:
    def __init__(
        self,
        config: Config,
        signal_engine: Optional[SignalEngine] = None,
        risk_manager: Optional[RiskManager] = None,
    ):
        self.config = config
        self.signal_engine = signal_engine or SignalEngine(config)
        self.risk_manager = risk_manager or RiskManager(config)

    def run(
        self,
        symbol_data: Dict[str, pd.DataFrame],
        *,
        starting_cash: float = 100_000.0,
    ) -> BacktestReport:
        if not symbol_data:
            raise ValueError("symbol_data is empty — nothing to backtest")

        indicator_data = {
            symbol: ind.compute_all(
                df, atr_period=self.config.atr_period, rsi_period=self.config.rsi_period,
                bb_period=self.config.bb_period, bb_num_std=self.config.bb_num_std,
                sma_trend_period=self.config.sma_trend_period,
            )
            for symbol, df in symbol_data.items()
        }

        symbols = list(indicator_data.keys())
        reference_index = indicator_data[symbols[0]].index
        for symbol in symbols[1:]:
            if not indicator_data[symbol].index.equals(reference_index):
                raise ValueError(
                    "Backtester requires every symbol's data to share an identical date index "
                    f"(symbol '{symbol}' differs from '{symbols[0]}'). Align or truncate calendars "
                    "to a common range before calling run() — this engine will not silently "
                    "misalign or forward-fill across mismatched trading calendars."
                )

        n = len(reference_index)
        if n < 2:
            raise ValueError("need at least 2 bars to run a backtest")

        cash = starting_cash
        open_positions: Dict[str, Position] = {}
        trades: List[Trade] = []
        circuit_breaker_triggers: List[dict] = []
        circuit_breaker = DrawdownCircuitBreaker(self.config)

        first_date = reference_index[0]
        first_date_str = first_date.isoformat() if hasattr(first_date, "isoformat") else str(first_date)
        equity_curve: List[EquityPoint] = [
            EquityPoint(date=first_date_str, equity=starting_cash, cash=starting_cash,
                       positions_value=0.0, drawdown_pct=0.0)
        ]
        peak_equity = starting_cash

        for t in range(0, n - 1):
            next_idx = t + 1
            next_date = reference_index[next_idx]
            next_date_str = next_date.isoformat() if hasattr(next_date, "isoformat") else str(next_date)

            current_equity = equity_curve[-1].equity
            current_exposure = equity_curve[-1].positions_value
            current_drawdown = (current_equity - peak_equity) / peak_equity if peak_equity > 0 else 0.0

            cb_status = circuit_breaker.check(_to_date(reference_index[t]), current_drawdown)
            if cb_status.newly_triggered:
                circuit_breaker_triggers.append({
                    "date": next_date_str, "drawdown_pct": cb_status.drawdown_pct,
                    "reason": cb_status.reason, "return_needed_to_recover_pct": cb_status.return_needed_to_recover_pct,
                })

            # ---- 1. EXITS (checked for every currently open position) ----
            for symbol in list(open_positions.keys()):
                position = open_positions[symbol]
                next_bar = indicator_data[symbol].iloc[next_idx]
                exit_price = None
                exit_reason = None

                if next_bar["low"] <= position.stop_loss_price:
                    reference = min(position.stop_loss_price, next_bar["open"])
                    exit_price = _fill_with_slippage(reference, self.config.slippage_bps, worse_for="seller")
                    exit_reason = ExitReason.STOP_LOSS
                elif next_bar["high"] >= position.take_profit_price:
                    reference = max(position.take_profit_price, next_bar["open"])
                    exit_price = _fill_with_slippage(reference, self.config.slippage_bps, worse_for="seller")
                    exit_reason = ExitReason.TAKE_PROFIT
                else:
                    decision_slice = indicator_data[symbol].iloc[: t + 1]
                    signal = self.signal_engine.generate(symbol, decision_slice)
                    if signal.direction == SignalDirection.SELL:
                        exit_price = _fill_with_slippage(next_bar["open"], self.config.slippage_bps, worse_for="seller")
                        exit_reason = ExitReason.SIGNAL_EXIT

                if exit_price is not None:
                    exit_commission = _commission(exit_price, position.quantity, self.config)
                    cash += position.quantity * exit_price - exit_commission
                    total_commission = position.entry_commission + exit_commission
                    pnl = (exit_price - position.entry_price) * position.quantity - total_commission
                    pnl_pct = ((exit_price - position.entry_price) / position.entry_price) * 100.0
                    trades.append(Trade(
                        symbol=symbol, entry_date=position.entry_date, exit_date=next_date_str,
                        entry_price=position.entry_price, exit_price=exit_price, quantity=position.quantity,
                        exit_reason=exit_reason, commission_paid=total_commission, pnl=pnl, pnl_pct=pnl_pct,
                        risk_amount=position.risk_amount,
                    ))
                    del open_positions[symbol]

            # ---- 2. ENTRIES (only symbols currently flat, only if the circuit breaker allows it) ----
            if not cb_status.halted:
                for symbol in symbols:
                    if symbol in open_positions:
                        continue
                    if not self.risk_manager.can_open_new_position(len(open_positions)):
                        break
                    decision_slice = indicator_data[symbol].iloc[: t + 1]
                    signal = self.signal_engine.generate(symbol, decision_slice)
                    if signal.direction != SignalDirection.BUY:
                        continue

                    entry_estimate_price = float(decision_slice["close"].iloc[-1])
                    atr_at_decision = float(decision_slice["atr"].iloc[-1])
                    size = self.risk_manager.size_position(
                        symbol, entry_price=entry_estimate_price, atr_value=atr_at_decision,
                        account_equity=current_equity, current_exposure_value=current_exposure,
                    )
                    if size.shares <= 0:
                        continue

                    next_bar = indicator_data[symbol].iloc[next_idx]
                    fill_price = _fill_with_slippage(next_bar["open"], self.config.slippage_bps, worse_for="buyer")
                    entry_commission = _commission(fill_price, size.shares, self.config)
                    cash -= size.shares * fill_price + entry_commission
                    stop_loss_price = fill_price - atr_at_decision * self.config.atr_stop_multiplier
                    take_profit_price = fill_price + atr_at_decision * self.config.atr_target_multiplier

                    open_positions[symbol] = Position(
                        symbol=symbol, quantity=size.shares, entry_price=fill_price, entry_date=next_date_str,
                        stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
                        risk_amount=size.risk_amount, entry_commission=entry_commission,
                    )
                    current_exposure += size.shares * fill_price

            # ---- 3. mark-to-market at next bar's CLOSE for the equity curve ----
            positions_value = float(sum(
                pos.quantity * indicator_data[sym]["close"].iloc[next_idx]
                for sym, pos in open_positions.items()
            ))
            equity = cash + positions_value
            peak_equity = max(peak_equity, equity)
            drawdown = (equity - peak_equity) / peak_equity if peak_equity > 0 else 0.0
            equity_curve.append(EquityPoint(
                date=next_date_str, equity=equity, cash=cash,
                positions_value=positions_value, drawdown_pct=drawdown,
            ))

        # ---- Force-close anything still open at the end of the data window ----
        last_idx = n - 1
        last_date = reference_index[last_idx]
        last_date_str = last_date.isoformat() if hasattr(last_date, "isoformat") else str(last_date)
        for symbol, position in list(open_positions.items()):
            last_close = indicator_data[symbol]["close"].iloc[last_idx]
            exit_price = _fill_with_slippage(last_close, self.config.slippage_bps, worse_for="seller")
            exit_commission = _commission(exit_price, position.quantity, self.config)
            cash += position.quantity * exit_price - exit_commission
            total_commission = position.entry_commission + exit_commission
            pnl = (exit_price - position.entry_price) * position.quantity - total_commission
            pnl_pct = ((exit_price - position.entry_price) / position.entry_price) * 100.0
            trades.append(Trade(
                symbol=symbol, entry_date=position.entry_date, exit_date=last_date_str,
                entry_price=position.entry_price, exit_price=exit_price, quantity=position.quantity,
                exit_reason=ExitReason.END_OF_BACKTEST, commission_paid=total_commission,
                pnl=pnl, pnl_pct=pnl_pct, risk_amount=position.risk_amount,
            ))
            del open_positions[symbol]
            updated_positions_value = equity_curve[-1].positions_value - position.quantity * last_close
            equity_curve[-1] = EquityPoint(
                date=equity_curve[-1].date, equity=cash + updated_positions_value, cash=cash,
                positions_value=updated_positions_value,
                drawdown_pct=equity_curve[-1].drawdown_pct,
            )

        report = perf.build_performance_report(
            equity_curve, trades, annual_risk_free_pct=self.config.annual_risk_free_rate_pct,
            periods_per_year=self.config.trading_days_per_year,
        )

        return BacktestReport(
            symbols=symbols,
            start_date=first_date_str, end_date=last_date_str,
            performance=report, trades=trades, equity_curve=equity_curve,
            circuit_breaker_triggers=circuit_breaker_triggers,
            config_snapshot={
                "risk_per_trade_pct": self.config.risk_per_trade_pct,
                "atr_stop_multiplier": self.config.atr_stop_multiplier,
                "atr_target_multiplier": self.config.atr_target_multiplier,
                "max_drawdown_circuit_breaker_pct": self.config.max_drawdown_circuit_breaker_pct,
                "commission_pct": self.config.commission_pct,
                "commission_flat": self.config.commission_flat,
                "slippage_bps": self.config.slippage_bps,
                "signal_buy_threshold": self.config.signal_buy_threshold,
                "signal_sell_threshold": self.config.signal_sell_threshold,
            },
        )
