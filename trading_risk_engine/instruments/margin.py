"""
Margin approximation.

**This is not real exchange margin, and this module says so at every
call site, on purpose.** Real F&O margin (SPAN + Exposure) is computed
daily by the exchange's own risk engine from a proprietary risk-array
methodology that reacts to volatility, portfolio offsets, and same-day
price moves — reproducing it faithfully is not a "write more code"
problem, it needs the exchange's own SPAN parameter files. Building a
false sense of precision here would be actively worse than a clearly
labeled rough figure, given what's at stake if someone sized a real
short-option position off a number that quietly understated the real
margin call.

What this module gives instead: a simple, configurable
percentage-of-notional-value estimate, good enough to make backtesting
and paper-trading position sizing *possible* and *directionally sane*.
Before ANY real order — this is said again in `trading_risk_engine/regulatory.py` and
the README, deliberately repeated rather than said once and assumed
remembered — check `KiteConnectBroker`'s real
`order_margins()`/`margins()` call for the actual number.

Long options are the one exception where this module IS exact, not
approximate: the maximum loss on a long option is the premium paid,
full stop, which is also exactly the margin required — there's no
SPAN methodology involved because there's no obligation beyond what's
already been paid.
"""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import FutureContract, OptionContract


@dataclass
class MarginConfig:
    approx_option_sell_margin_pct: float = 0.14  # rough, index-option-scale ballpark — see module docstring
    approx_futures_margin_pct: float = 0.12
    min_absolute_margin_per_lot: float = 15_000.0  # a floor so a very-low-premium short option doesn't look "free"


def option_margin_required(
    contract: OptionContract, side: str, num_lots: int, premium: float, spot_price: float,
    margin_config: MarginConfig = None,
) -> float:
    """side is 'LONG' (buying) or 'SHORT' (selling/writing)."""
    margin_config = margin_config or MarginConfig()
    quantity = num_lots * contract.lot_size
    if side == "LONG":
        return premium * quantity  # exact — see module docstring
    notional = spot_price * quantity
    approx = notional * margin_config.approx_option_sell_margin_pct
    return max(approx, margin_config.min_absolute_margin_per_lot * num_lots)


def future_margin_required(
    contract: FutureContract, num_lots: int, futures_price: float, margin_config: MarginConfig = None,
) -> float:
    margin_config = margin_config or MarginConfig()
    quantity = num_lots * contract.lot_size
    notional = futures_price * quantity
    approx = notional * margin_config.approx_futures_margin_pct
    return max(approx, margin_config.min_absolute_margin_per_lot * num_lots)
