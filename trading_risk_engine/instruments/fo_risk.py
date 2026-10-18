"""
F&O position sizing.

Same fixed-fractional PRINCIPLE as trading_risk_engine/risk.py (risk a defined
fraction of equity, sized from a defined stop distance), applied to
instruments where "stop distance" doesn't mean the same thing it does
for a stock:

- Long options: risk is naturally bounded (max loss = premium paid), so
  sizing is driven by how much of that bounded loss you're willing to
  risk before exiting early — the stop is a percentage of premium, not a
  price distance.
- Short options: risk is NOT naturally bounded (a short call in
  particular), so a stop is mandatory, not optional, and sizing is
  driven by the loss AT that stop — not by the premium received, which
  would badly understate the real risk.
- Futures: track the underlying near 1:1, so the equity ATR-stop
  playbook applies almost unchanged, just lot-sized.

Every path also checks MARGIN utilization against
`FOConfig.max_margin_utilization_pct` — a distinct safety rail from the
per-trade risk percentage, because a margin call liquidates at whatever
price is available, not at your intended stop, and equity (a cash
purchase, no margin) has no equivalent failure mode to guard against.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from .contracts import FutureContract, OptionContract
from .fo_config import FOConfig
from .margin import MarginConfig, future_margin_required, option_margin_required


def _is_invalid(value) -> bool:
    """True for None, NaN, or non-positive — the ONE guard every sizing
    entry point below uses for every numeric input, after a real bug was
    found here: `value <= 0` alone silently lets NaN through, since every
    comparison against NaN evaluates to False in Python (equity Phase
    6's risk.py already guarded against this explicitly; missed here on
    first pass and caught by tests/test_fo_risk.py actually exercising a
    NaN input, not by inspection)."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return value <= 0


@dataclass
class FOPositionSizeDecision:
    contract: object
    side: str
    num_lots: int
    entry_price_estimate: float
    stop_price: float
    take_profit_price: float
    margin_required: float
    risk_amount: float
    reasons: List[str] = field(default_factory=list)


def _apply_margin_cap(
    num_lots: int, margin_per_lot: float, account_equity: float, current_margin_used: float,
    fo_config: FOConfig, reasons: List[str],
) -> int:
    if num_lots <= 0 or margin_per_lot <= 0:
        return num_lots
    max_total_margin = account_equity * fo_config.max_margin_utilization_pct
    remaining_margin_budget = max(0.0, max_total_margin - current_margin_used)
    max_lots_by_margin = math.floor(remaining_margin_budget / margin_per_lot)
    if max_lots_by_margin < num_lots:
        reasons.append(
            f"capped by max_margin_utilization_pct ({fo_config.max_margin_utilization_pct:.0%}): "
            f"{num_lots} -> {max_lots_by_margin} lots (remaining margin budget "
            f"{remaining_margin_budget:,.0f})"
        )
        num_lots = max(0, max_lots_by_margin)
    if num_lots > fo_config.max_lots_per_position:
        reasons.append(f"capped by max_lots_per_position: {num_lots} -> {fo_config.max_lots_per_position}")
        num_lots = fo_config.max_lots_per_position
    return num_lots


class FORiskManager:
    def __init__(self, fo_config: FOConfig, margin_config: Optional[MarginConfig] = None):
        self.fo_config = fo_config
        self.margin_config = margin_config or MarginConfig()

    def size_long_option(
        self, contract: OptionContract, entry_premium: float, spot: float,
        account_equity: float, current_margin_used: float = 0.0,
    ) -> FOPositionSizeDecision:
        reasons = []
        if _is_invalid(entry_premium) or _is_invalid(account_equity):
            return FOPositionSizeDecision(contract, "LONG", 0, entry_premium, 0.0, 0.0, 0.0, 0.0,
                                          ["non-positive or invalid premium/equity — no trade"])

        risk_amount = account_equity * self.fo_config.long_option_risk_per_trade_pct
        stop_price = entry_premium * (1.0 - self.fo_config.long_option_stop_loss_pct)
        take_profit_price = entry_premium * (1.0 + self.fo_config.long_option_take_profit_pct)
        max_loss_per_unit = entry_premium - stop_price
        max_loss_per_lot = max_loss_per_unit * contract.lot_size
        if max_loss_per_lot <= 0:
            return FOPositionSizeDecision(contract, "LONG", 0, entry_premium, stop_price, take_profit_price,
                                          0.0, 0.0, ["stop distance is zero — no trade"])

        num_lots = math.floor(risk_amount / max_loss_per_lot)
        reasons.append(
            f"long option: risk {self.fo_config.long_option_risk_per_trade_pct:.2%} of equity "
            f"({risk_amount:,.2f}) / max loss per lot ({max_loss_per_lot:,.2f}) = {num_lots} lots"
        )

        margin_per_lot = option_margin_required(contract, "LONG", 1, entry_premium, spot, self.margin_config)
        num_lots = _apply_margin_cap(num_lots, margin_per_lot, account_equity, current_margin_used,
                                     self.fo_config, reasons)
        if num_lots <= 0:
            reasons.append("risk budget or margin headroom too small for even 1 lot — no trade")

        return FOPositionSizeDecision(
            contract=contract, side="LONG", num_lots=max(0, num_lots), entry_price_estimate=entry_premium,
            stop_price=stop_price, take_profit_price=take_profit_price,
            margin_required=margin_per_lot * max(0, num_lots), risk_amount=max_loss_per_lot * max(0, num_lots),
            reasons=reasons,
        )

    def size_short_option(
        self, contract: OptionContract, entry_premium: float, spot: float,
        account_equity: float, current_margin_used: float = 0.0,
    ) -> FOPositionSizeDecision:
        reasons = []
        if _is_invalid(entry_premium) or _is_invalid(account_equity):
            return FOPositionSizeDecision(contract, "SHORT", 0, entry_premium, 0.0, 0.0, 0.0, 0.0,
                                          ["non-positive or invalid premium/equity — no trade"])

        risk_amount = account_equity * self.fo_config.short_option_risk_per_trade_pct
        stop_price = entry_premium * self.fo_config.short_option_stop_loss_multiple
        take_profit_price = entry_premium * (1.0 - self.fo_config.short_option_take_profit_pct)
        max_loss_per_unit = stop_price - entry_premium  # buying back higher than the premium received
        max_loss_per_lot = max_loss_per_unit * contract.lot_size
        if max_loss_per_lot <= 0:
            return FOPositionSizeDecision(contract, "SHORT", 0, entry_premium, stop_price, take_profit_price,
                                          0.0, 0.0, ["degenerate stop configuration — no trade"])

        num_lots = math.floor(risk_amount / max_loss_per_lot)
        reasons.append(
            f"short option: risk {self.fo_config.short_option_risk_per_trade_pct:.2%} of equity "
            f"({risk_amount:,.2f}) / loss-at-stop per lot ({max_loss_per_lot:,.2f}) = {num_lots} lots "
            "— sized from the loss AT THE STOP, never from premium received alone"
        )

        margin_per_lot = option_margin_required(contract, "SHORT", 1, entry_premium, spot, self.margin_config)
        num_lots = _apply_margin_cap(num_lots, margin_per_lot, account_equity, current_margin_used,
                                     self.fo_config, reasons)
        if num_lots <= 0:
            reasons.append("risk budget or margin headroom too small for even 1 lot — no trade")

        return FOPositionSizeDecision(
            contract=contract, side="SHORT", num_lots=max(0, num_lots), entry_price_estimate=entry_premium,
            stop_price=stop_price, take_profit_price=take_profit_price,
            margin_required=margin_per_lot * max(0, num_lots), risk_amount=max_loss_per_lot * max(0, num_lots),
            reasons=reasons,
        )

    def size_future(
        self, contract: FutureContract, side: str, entry_price: float, atr_value: float,
        account_equity: float, current_margin_used: float = 0.0,
    ) -> FOPositionSizeDecision:
        reasons = []
        if _is_invalid(entry_price) or _is_invalid(account_equity) or _is_invalid(atr_value):
            return FOPositionSizeDecision(contract, side, 0, entry_price, entry_price, entry_price, 0.0, 0.0,
                                          ["non-positive price/equity or invalid ATR — no trade"])

        risk_amount = account_equity * self.fo_config.futures_risk_per_trade_pct
        stop_distance = atr_value * self.fo_config.futures_atr_stop_multiplier
        target_distance = atr_value * self.fo_config.futures_atr_target_multiplier
        if side == "LONG":
            stop_price = entry_price - stop_distance
            take_profit_price = entry_price + target_distance
        else:
            stop_price = entry_price + stop_distance
            take_profit_price = entry_price - target_distance

        max_loss_per_lot = stop_distance * contract.lot_size
        num_lots = math.floor(risk_amount / max_loss_per_lot)
        reasons.append(
            f"future ({side}): risk {self.fo_config.futures_risk_per_trade_pct:.2%} of equity "
            f"({risk_amount:,.2f}) / ATR-based loss per lot ({max_loss_per_lot:,.2f}) = {num_lots} lots"
        )

        margin_per_lot = future_margin_required(contract, 1, entry_price, self.margin_config)
        num_lots = _apply_margin_cap(num_lots, margin_per_lot, account_equity, current_margin_used,
                                     self.fo_config, reasons)
        if num_lots <= 0:
            reasons.append("risk budget or margin headroom too small for even 1 lot — no trade")

        return FOPositionSizeDecision(
            contract=contract, side=side, num_lots=max(0, num_lots), entry_price_estimate=entry_price,
            stop_price=stop_price, take_profit_price=take_profit_price,
            margin_required=margin_per_lot * max(0, num_lots), risk_amount=max_loss_per_lot * max(0, num_lots),
            reasons=reasons,
        )

    def can_open_new_position(self, current_open_count: int) -> bool:
        return current_open_count < self.fo_config.max_concurrent_fo_positions
