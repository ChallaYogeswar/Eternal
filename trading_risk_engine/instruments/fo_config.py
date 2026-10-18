"""
F&O risk configuration — deliberately a SEPARATE dataclass from
trading_risk_engine/config.py's `Config`, not new fields bolted onto it. Equity risk
is share-count-and-ATR-dollar-distance; F&O risk is lot-count-and-
premium-percentage (options) or lot-count-and-margin (futures) — close
enough in spirit that they can share the same signal engine and gateway,
but different enough in kind that cramming both into one config class
would make neither reading clearly. This is the concrete first
application of the "instrument adapter" principle: shared core, separate
per-instrument-type configuration.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FOConfig:
    # Long options: max loss is bounded and exact (the premium paid), so
    # position sizing directly follows from that — but retail practice
    # is still to exit well before a total loss, not ride a long option
    # to zero. Both are expressed as a fraction of the ENTRY premium.
    long_option_risk_per_trade_pct: float = 0.01  # of account equity, same floor equity Phase 6 uses
    long_option_stop_loss_pct: float = 0.50  # exit if premium falls 50% from entry
    long_option_take_profit_pct: float = 1.00  # exit if premium doubles from entry

    # Short (written) options: the build plan's own math-correction
    # research (93% of F&O retail traders lose money) is disproportionately
    # about exactly this activity — theoretically unlimited loss on a
    # short call, large-but-bounded on a short put. A stop is NOT
    # optional here the way it can be debated for long options; sizing
    # is driven entirely by the loss AT the stop, never by premium
    # received alone.
    short_option_risk_per_trade_pct: float = 0.01
    short_option_stop_loss_multiple: float = 2.5  # exit if premium rises to 2.5x what was received
    short_option_take_profit_pct: float = 0.50  # exit once premium has decayed 50% toward zero (standard retail practice: bank most of the gain, don't hold to expiry chasing the last few rupees against gamma risk)

    # Futures: ATR-based, same shape as equity's stop/target — futures
    # track the underlying close to 1:1 (basis aside), so the equity
    # playbook translates directly, just lot-sized instead of share-sized.
    futures_risk_per_trade_pct: float = 0.01
    futures_atr_stop_multiplier: float = 1.5
    futures_atr_target_multiplier: float = 3.0

    # Margin safety rails — specific to F&O, no equity equivalent. A
    # margin call forces liquidation at whatever price is available, not
    # at your intended stop — capping utilization is a distinct
    # safeguard from the per-trade risk % above, not a restatement of it.
    max_margin_utilization_pct: float = 0.50  # never commit more than half of equity to margin at once
    max_lots_per_position: int = 20
    max_concurrent_fo_positions: int = 5

    def __post_init__(self):
        for name, value in (
            ("long_option_risk_per_trade_pct", self.long_option_risk_per_trade_pct),
            ("short_option_risk_per_trade_pct", self.short_option_risk_per_trade_pct),
            ("futures_risk_per_trade_pct", self.futures_risk_per_trade_pct),
        ):
            if value > 0.02:
                raise ValueError(
                    f"{name} ({value}) exceeds the 2% hard ceiling Eternal_Master_Build_Plan.md Section 7.2 "
                    "names for risk per trade — this applies to F&O exactly as it does to equity, arguably "
                    "more so given the build plan's own math-correction research on F&O loss rates."
                )
        if self.max_margin_utilization_pct > 0.75:
            raise ValueError(
                f"max_margin_utilization_pct ({self.max_margin_utilization_pct}) is dangerously high — "
                "a margin call liquidates at whatever price is available, not at your intended stop; "
                "leaving headroom is the entire point of this cap"
            )
