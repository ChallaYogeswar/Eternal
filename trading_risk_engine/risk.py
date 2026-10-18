"""
Risk management: position sizing, stop/target placement, and the
max-drawdown circuit breaker.

Eternal_Master_Build_Plan.md Section 7.2, verbatim: "fixed-fractional
position sizing (1-2% risk/trade, ATR-based), stop-loss and take-profit
set at entry and never moved, a max-drawdown circuit breaker that stops
trading for the period rather than pushing harder, and judging the
strategy over dozens of trades and months — never a single week."

Every one of those four clauses has a direct, testable implementation
below: `RiskManager.size_position` (fixed-fractional, ATR-based),
`compute_stop_and_target` (computed once at entry; backtester.py never
calls this again for an open position), `DrawdownCircuitBreaker` (halts
NEW entries for a configurable period, never partially), and
performance.py's report itself (nothing here computes or surfaces a
single-week return figure as if it meant anything).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime
from typing import Optional, Tuple

from .config import Config
from .models import PositionSizeDecision
from .performance import recovery_return_needed


def compute_stop_and_target(entry_price: float, atr_value: float, config: Config) -> Tuple[float, float]:
    """Computed ONCE, at entry. Eternal_Master_Build_Plan.md Section 7.2:
    "stop-loss and take-profit set at entry and never moved" — this
    function has no notion of an already-open position to move a stop
    for; backtester.py and paper_trader.py call it exactly once, at the
    moment a position opens, and store the result on the Position object
    itself rather than recomputing it on later bars."""
    stop_loss_price = entry_price - atr_value * config.atr_stop_multiplier
    take_profit_price = entry_price + atr_value * config.atr_target_multiplier
    return stop_loss_price, take_profit_price


class RiskManager:
    def __init__(self, config: Config):
        self.config = config

    def size_position(
        self,
        symbol: str,
        entry_price: float,
        atr_value: float,
        account_equity: float,
        current_exposure_value: float = 0.0,
    ) -> PositionSizeDecision:
        """The core fixed-fractional, ATR-based sizing formula:

            risk_amount      = account_equity * risk_per_trade_pct
            stop_distance    = atr_value * atr_stop_multiplier
            shares           = floor(risk_amount / stop_distance)

        Then two caps are applied, in order, BEYOND the build plan's own
        literal formula — professional portfolio-construction hygiene
        layered on top of it, documented as such rather than silently
        blended in:

        1. max_position_pct_of_equity — without this, a stock with a
           very tight stop distance relative to its price (a low-
           volatility stock, or an entry price so high that ATR looks
           small by comparison) can produce a share count whose total
           value dwarfs the account, even though the RISK amount is
           still correctly 1-2%. A blown-up position size is its own
           risk (concentration, liquidity, a single circuit-filter halt
           wiping out far more than the intended 1-2%) independent of
           the stop-distance math being followed correctly.
        2. max_total_exposure_pct_of_equity minus whatever's already
           deployed in other open positions — the per-trade formula
           above has no visibility into what else is already held;
           this is where that portfolio-level view gets applied.

        Returns a PositionSizeDecision with shares=0 (never a negative
        count, never an exception) if the risk budget is too small for
        the stop distance at this price, or if atr_value is invalid
        (<=0 or NaN) — a "no trade" answer is safe; guessing a share
        count from bad volatility data is not.
        """
        reasons = []

        if atr_value is None or (isinstance(atr_value, float) and math.isnan(atr_value)) or atr_value <= 0:
            return PositionSizeDecision(
                symbol=symbol, shares=0, entry_price=entry_price,
                stop_loss_price=entry_price, take_profit_price=entry_price,
                risk_amount=0.0, risk_per_share=0.0, position_value=0.0,
                account_equity_at_decision=account_equity,
                reasons=["invalid or missing ATR value — cannot size a stop-based position; no trade"],
            )
        if entry_price <= 0 or account_equity <= 0:
            return PositionSizeDecision(
                symbol=symbol, shares=0, entry_price=entry_price,
                stop_loss_price=entry_price, take_profit_price=entry_price,
                risk_amount=0.0, risk_per_share=0.0, position_value=0.0,
                account_equity_at_decision=account_equity,
                reasons=["non-positive entry price or account equity — no trade"],
            )

        risk_amount = account_equity * self.config.risk_per_trade_pct
        stop_distance = atr_value * self.config.atr_stop_multiplier
        shares = math.floor(risk_amount / stop_distance)
        reasons.append(
            f"fixed-fractional: risk {self.config.risk_per_trade_pct:.2%} of equity "
            f"({risk_amount:.2f}) / ATR-based stop distance ({stop_distance:.2f}) = {shares} shares"
        )

        position_value = shares * entry_price
        max_position_value = account_equity * self.config.max_position_pct_of_equity
        if position_value > max_position_value and entry_price > 0:
            capped_shares = math.floor(max_position_value / entry_price)
            reasons.append(
                f"capped by max_position_pct_of_equity ({self.config.max_position_pct_of_equity:.0%}): "
                f"{shares} -> {capped_shares} shares"
            )
            shares = capped_shares
            position_value = shares * entry_price

        max_total_exposure_value = account_equity * self.config.max_total_exposure_pct_of_equity
        remaining_exposure_room = max(0.0, max_total_exposure_value - current_exposure_value)
        if position_value > remaining_exposure_room and entry_price > 0:
            capped_shares = math.floor(remaining_exposure_room / entry_price)
            reasons.append(
                f"capped by max_total_exposure_pct_of_equity: remaining room {remaining_exposure_room:.2f} "
                f"-> {capped_shares} shares"
            )
            shares = capped_shares
            position_value = shares * entry_price

        stop_loss_price, take_profit_price = compute_stop_and_target(entry_price, atr_value, self.config)
        actual_risk_amount = shares * stop_distance

        if shares <= 0:
            reasons.append("risk budget too small for this stop distance at this price — no trade")

        return PositionSizeDecision(
            symbol=symbol, shares=max(0, shares), entry_price=entry_price,
            stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
            risk_amount=actual_risk_amount, risk_per_share=stop_distance,
            position_value=position_value, account_equity_at_decision=account_equity,
            reasons=reasons,
        )

    def can_open_new_position(self, current_open_position_count: int) -> bool:
        return current_open_position_count < self.config.max_concurrent_positions


@dataclass
class CircuitBreakerStatus:
    halted: bool
    newly_triggered: bool
    period_key: str
    drawdown_pct: float
    reason: str
    return_needed_to_recover_pct: Optional[float] = None


class DrawdownCircuitBreaker:
    """Eternal_Master_Build_Plan.md Section 7.2: "a max-drawdown circuit
    breaker that stops trading for the period rather than pushing
    harder." Deliberately TIME-based re-arming, not recovery-based: once
    the configured period (week or month) rolls over, new entries are
    allowed again regardless of whether the drawdown has actually
    improved. This is the literal build-plan wording ("for the period"),
    and it is the more conservative reading available: a
    recovery-conditional re-arm ("stay halted until back to a smaller
    drawdown") could keep a struggling strategy from ever being allowed
    to trade again after a bad enough month, which is a stronger
    intervention than the build plan actually calls for and one this
    project doesn't make unilaterally.

    Halting only blocks NEW entries. It never blocks closing an existing
    position (stop-loss, take-profit, or a signal-based exit) — freezing
    exits during a drawdown would trap the account in whatever it happens
    to be holding, which is the opposite of risk reduction.
    """

    def __init__(self, config: Config):
        self.config = config
        self._halted_period_key: Optional[str] = None

    def _period_key(self, on_date: date_type) -> str:
        if self.config.circuit_breaker_period == "week":
            iso = on_date.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"
        return on_date.strftime("%Y-%m")

    def check(self, on_date: date_type, current_drawdown_pct: float) -> CircuitBreakerStatus:
        """`current_drawdown_pct` is negative-or-zero (e.g. -0.12 for a
        12% drawdown from the equity peak), matching
        performance.max_drawdown_and_duration's own convention."""
        period = self._period_key(on_date)

        if self._halted_period_key is not None and self._halted_period_key != period:
            self._halted_period_key = None  # the period rolled over -> re-arm

        breaker_threshold = -abs(self.config.max_drawdown_circuit_breaker_pct)

        if self._halted_period_key == period:
            return CircuitBreakerStatus(
                halted=True, newly_triggered=False, period_key=period,
                drawdown_pct=current_drawdown_pct,
                reason=f"circuit breaker already tripped for {period}; new entries paused for the rest of the period",
            )

        if current_drawdown_pct <= breaker_threshold:
            self._halted_period_key = period
            recovery_pct = recovery_return_needed(min(abs(current_drawdown_pct), 0.999999)) * 100.0
            return CircuitBreakerStatus(
                halted=True, newly_triggered=True, period_key=period,
                drawdown_pct=current_drawdown_pct,
                reason=(
                    f"drawdown {current_drawdown_pct:.2%} breached the "
                    f"{self.config.max_drawdown_circuit_breaker_pct:.0%} circuit-breaker threshold — "
                    f"new entries paused for the rest of this {self.config.circuit_breaker_period}. "
                    f"Recovering from here requires a +{recovery_pct:.1f}% gain — pushing harder now, "
                    "not pausing, is exactly the mistake Eternal_Master_Build_Plan.md Section 7.2 warns against."
                ),
                return_needed_to_recover_pct=recovery_pct,
            )

        return CircuitBreakerStatus(
            halted=False, newly_triggered=False, period_key=period,
            drawdown_pct=current_drawdown_pct, reason="within normal drawdown tolerance",
        )

    def reset(self) -> None:
        """Manual override — a person (not a strategy loop) deciding to
        re-arm early. Never called automatically by anything in this
        phase; exposed for completeness and for tests."""
        self._halted_period_key = None
