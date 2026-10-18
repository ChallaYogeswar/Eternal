"""
Phase 6 data models.

Eternal_Master_Build_Plan.md Section 7.2: "Signals as explicit, auditable
rules, not a black box." Every model here is a plain, inspectable
dataclass — no opaque state, nothing that can't be logged verbatim to the
ledger the same way Phase 1's ledger already logs every QAP-P/RCAP-P
event. A Signal in particular always carries a human-readable `reasons`
list, not just a direction — the same "why," not just "what," discipline
RCAP-P's ATTRIBUTE ring and QAP-P's trust score both already apply.
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


class SignalDirection(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    EXIT = "EXIT"  # close an existing position on a signal, not a stop/target


class OrderSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, enum.Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ExitReason(str, enum.Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    SIGNAL_EXIT = "SIGNAL_EXIT"
    END_OF_BACKTEST = "END_OF_BACKTEST"
    MANUAL = "MANUAL"


@dataclass
class OHLCVBar:
    date: str  # ISO date, kept as a string at the model boundary for clean logging/hashing
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class IndicatorSnapshot:
    """One bar's worth of computed indicator values, keyed by name
    (e.g. 'rsi_14', 'atr_14', 'macd_line'). Kept as a flat dict rather
    than named fields so signals.py's rule engine can reference any
    indicator generically without this module needing to know the full
    set in advance."""
    values: Dict[str, Optional[float]] = field(default_factory=dict)


@dataclass
class Signal:
    symbol: str
    date: str
    direction: SignalDirection
    score: float  # -1.0 (strong sell) .. +1.0 (strong buy); 0.0 = neutral
    reasons: List[str] = field(default_factory=list)
    indicator_snapshot: Dict[str, Optional[float]] = field(default_factory=dict)

    def as_ledger_payload(self) -> dict:
        return {
            "symbol": self.symbol, "date": self.date, "direction": self.direction.value,
            "score": round(self.score, 4), "reasons": list(self.reasons),
            "indicators": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.indicator_snapshot.items()},
        }


@dataclass
class PositionSizeDecision:
    """Output of risk.py's position sizing — kept separate from Position
    itself so 'what the formula said' and 'what actually got filled' are
    always distinguishable in logs, the same reason WriteCallRequest and
    WriteCallResult are two different types in Phase 4."""
    symbol: str
    shares: int
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    risk_amount: float
    risk_per_share: float
    position_value: float
    account_equity_at_decision: float
    reasons: List[str] = field(default_factory=list)


@dataclass
class Order:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType
    limit_price: Optional[float]
    status: OrderStatus
    requested_at: float = field(default_factory=time.time)
    filled_at: Optional[float] = None
    fill_price: Optional[float] = None
    commission: float = 0.0
    slippage: float = 0.0
    broker_order_id: Optional[str] = None
    reject_reason: Optional[str] = None
    tag: str = ""


@dataclass
class Position:
    symbol: str
    quantity: int
    entry_price: float
    entry_date: str
    stop_loss_price: float
    take_profit_price: float
    # Fixed at entry, never moved — Eternal_Master_Build_Plan.md Section
    # 7.2: "stop-loss and take-profit set at entry and never moved."
    risk_amount: float
    entry_commission: float = 0.0

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.entry_price


@dataclass
class Trade:
    """A completed round trip (entry + exit) — the unit performance.py's
    metrics are computed over."""
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    exit_reason: ExitReason
    commission_paid: float
    pnl: float
    pnl_pct: float
    risk_amount: float

    @property
    def r_multiple(self) -> float:
        """PnL expressed in units of the risk taken on this specific
        trade — the standard way to compare trades sized differently.
        A trade risking ₹1,000 that made ₹2,000 is a +2R trade regardless
        of position size, letting expectancy be compared apples-to-apples
        across trades with different ATR/entry prices."""
        if self.risk_amount <= 0:
            return 0.0
        return self.pnl / self.risk_amount


@dataclass
class EquityPoint:
    date: str
    equity: float
    cash: float
    positions_value: float
    drawdown_pct: float


@dataclass
class PerformanceReport:
    total_return_pct: float
    cagr_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    max_drawdown_duration_days: int
    win_rate_pct: float
    profit_factor: float
    expectancy_r: float
    avg_win: float
    avg_loss: float
    num_trades: int
    num_winning_trades: int
    num_losing_trades: int
    kelly_fraction: Optional[float]
    starting_equity: float
    ending_equity: float
    num_days: int


@dataclass
class BacktestReport:
    symbols: List[str]
    start_date: str
    end_date: str
    performance: PerformanceReport
    trades: List[Trade]
    equity_curve: List[EquityPoint]
    circuit_breaker_triggers: List[dict]
    config_snapshot: dict
