"""
Trading engine MCP server for Phase 6.

Same shape as mcp_servers/laptop_agent/server.py and
mcp_servers/home_assistant/server.py (Phase 5): a thin FastMCP wrapper
that calls into data.py and translates data.TradingEngineError into
ValueError (this project's established "surface a caller-facing reason,
not an unhandled exception" convention) — no policy decisions happen
here.

Tool sensitivity tiers are assigned in trading_risk_engine/registry_ext.py, not here;
this file does not know or care which tools are gated behind what — see
that module and trading_risk_engine/gateway.py for the actual security architecture.
"""
import sys
from pathlib import Path
from typing import Annotated, List, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data  # noqa: E402

mcp = FastMCP("trading_engine_mcp")


class QuoteResult(BaseModel):
    symbol: str
    price: float
    as_of: str
    note: Optional[str] = None


class SignalResult(BaseModel):
    symbol: str
    date: str
    direction: str
    score: float
    reasons: List[str]


class RecoveryMathResult(BaseModel):
    drawdown_pct: float
    period_days: int
    return_needed_to_recover_pct: float
    is_unrealistic_for_this_period: bool
    note: str


class TradeModel(BaseModel):
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    exit_reason: str
    commission_paid: float
    pnl: float
    pnl_pct: float
    risk_amount: float


class PerformanceReportModel(BaseModel):
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
    kelly_fraction: Optional[float] = None
    starting_equity: float
    ending_equity: float
    num_days: int


class BacktestResult(BaseModel):
    symbols: List[str]
    start_date: str
    end_date: str
    performance: PerformanceReportModel
    num_trades: int
    circuit_breaker_triggers: List[dict]
    trades: List[TradeModel]


class OrderResult(BaseModel):
    order_id: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    status: str
    fill_price: Optional[float] = None
    commission: Optional[float] = None
    reject_reason: Optional[str] = None
    broker_order_id: Optional[str] = None


class PaperPositionModel(BaseModel):
    symbol: str
    quantity: int
    entry_price: float
    entry_date: str
    stop_loss_price: float
    take_profit_price: float
    risk_amount: float


class PaperMarginsModel(BaseModel):
    cash: float


class DailyReauthStatusModel(BaseModel):
    configured: bool
    note: Optional[str] = None
    has_token: Optional[bool] = None
    obtained_at_ist: Optional[str] = None
    valid_until_ist: Optional[str] = None
    is_valid_now: Optional[bool] = None
    hours_remaining: Optional[float] = None


class IPChangeRecordModel(BaseModel):
    ip_address: str
    recorded_at_utc: str


class IPChangeResult(BaseModel):
    allowed: bool
    reason: str


class DayFillModel(BaseModel):
    symbol: str
    action: str
    order: dict


class PendingDecisionModel(BaseModel):
    symbol: str
    action: str
    shares: Optional[int] = None


class PaperTradingDayResult(BaseModel):
    date: str
    fills: List[DayFillModel]
    new_pending_decisions: List[PendingDecisionModel]
    circuit_breaker: dict
    equity: float
    cash: float
    positions_value: float


# -- F&O extension (Index Options + Index Futures) --------------------------

class OptionChainQuoteModel(BaseModel):
    strike: float
    option_type: str
    theoretical_price: float
    implied_volatility: float
    delta: float
    gamma: float
    theta_per_day: float
    vega_per_pct: float
    rho_per_pct: float


class OptionChainResult(BaseModel):
    underlying_symbol: str
    spot: float
    expiry: str
    as_of: str
    quotes: List[OptionChainQuoteModel]


class FOContractModel(BaseModel):
    underlying_symbol: str
    kind: str
    strike: Optional[float] = None
    expiry: str
    option_type: Optional[str] = None
    display_symbol: str


class FOTradeModel(BaseModel):
    contract: FOContractModel
    side: str
    num_lots: int
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    exit_reason: str
    margin_required: float
    risk_amount: float
    pnl: float
    r_multiple: float


class FOEquityPointModel(BaseModel):
    date: str
    cash: float
    margin_blocked: float
    mark_to_market_value: float
    equity: float


class FOBacktestSummaryModel(BaseModel):
    num_trades: int
    num_winning_trades: int
    num_losing_trades: int
    win_rate_pct: float
    total_pnl: float
    avg_win: float
    avg_loss: float


class FOPerformanceModel(BaseModel):
    total_return_pct: float
    cagr_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    max_drawdown_duration_days: int
    profit_factor: float
    expectancy_r: float
    kelly_fraction: Optional[float] = None
    num_days: int


class FOBacktestResult(BaseModel):
    underlying_symbol: str
    strategy_style: Optional[str] = None
    starting_equity: float
    ending_equity: float
    total_return_pct: float
    summary: FOBacktestSummaryModel
    performance: Optional[FOPerformanceModel] = None
    trades: List[FOTradeModel]
    equity_curve: List[FOEquityPointModel]


class FOPaperPositionModel(BaseModel):
    position_id: str
    kind: str
    underlying_symbol: str
    strike: Optional[float] = None
    expiry: str
    option_type: Optional[str] = None
    side: str
    num_lots: int
    lot_size: int
    entry_price: float
    entry_date: str
    margin_blocked: float
    stop_loss_price: float
    take_profit_price: float


class FOPaperMarginsModel(BaseModel):
    cash: float
    margin_blocked: float
    available: float


class FOPaperOrderResult(BaseModel):
    order_id: str
    display_symbol: str
    side: str
    num_lots: int
    price: float
    status: str
    reject_reason: Optional[str] = None


class FOLiveOrderResult(BaseModel):
    order_id: str
    symbol: str
    display_symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: Optional[float] = None
    status: str
    fill_price: Optional[float] = None
    commission: Optional[float] = None
    reject_reason: Optional[str] = None
    broker_order_id: Optional[str] = None


class FOClosePositionResult(BaseModel):
    position_id: str
    display_symbol: str
    exit_price: float
    pnl: Optional[float] = None


def _order_dict_to_result(d: dict) -> OrderResult:
    return OrderResult(
        order_id=d.get("order_id", ""), symbol=d["symbol"], side=d["side"], quantity=d["quantity"],
        order_type=d["order_type"], status=d["status"], fill_price=d.get("fill_price"),
        commission=d.get("commission"), reject_reason=d.get("reject_reason"),
        broker_order_id=d.get("broker_order_id"),
    )


@mcp.tool(
    name="trading_get_quote",
    annotations={"title": "Get a market quote", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": False, "openWorldHint": True},
)
async def trading_get_quote(symbol: Annotated[str, Field(description="e.g. 'RELIANCE.NS'")]) -> QuoteResult:
    """Latest price for a symbol. Uses the configured MarketDataProvider —
    SIMULATED by default in this build (see trading_risk_engine/data_provider.py);
    swap in YFinanceProvider for a real deployment with normal internet
    access."""
    try:
        return QuoteResult(**data.get_engine().get_quote(symbol))
    except data.TradingEngineError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="trading_get_signal",
    annotations={"title": "Get the current rule-based signal for a symbol", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def trading_get_signal(symbol: Annotated[str, Field(description="e.g. 'TCS.NS'")]) -> SignalResult:
    """Runs the explicit, auditable rule engine (trend/RSI/MACD/Bollinger/
    Stochastic — see trading_risk_engine/signals.py) against the latest data and
    returns BUY/SELL/HOLD with every rule's reasoning, not just a number."""
    try:
        return SignalResult(**data.get_engine().get_signal(symbol))
    except data.TradingEngineError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="trading_run_backtest",
    annotations={"title": "Run a historical backtest", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def trading_run_backtest(
    symbols: Annotated[List[str], Field(description="e.g. ['RELIANCE.NS', 'TCS.NS']")],
    start_date: Annotated[str, Field(description="ISO date, e.g. '2023-01-01'")],
    end_date: Annotated[str, Field(description="ISO date, e.g. '2025-01-01'")],
    starting_cash: Annotated[float, Field(default=100_000.0)] = 100_000.0,
) -> BacktestResult:
    """Full event-driven backtest: fixed-fractional ATR-based position
    sizing, gap-aware stop/target execution, commission and slippage, a
    max-drawdown circuit breaker, and a complete performance report
    (Sharpe, Sortino, Calmar, max drawdown, win rate, profit factor,
    expectancy, and an informational Kelly figure). Never mandatory
    before going live per Eternal_Master_Build_Plan.md Section 7.2 — it
    IS the mandatory step."""
    try:
        return BacktestResult(**data.get_engine().run_backtest(symbols, start_date, end_date, starting_cash))
    except data.TradingEngineError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="trading_check_recovery_math",
    annotations={"title": "Check the real math behind a loss-recovery target", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def trading_check_recovery_math(
    drawdown_pct: Annotated[float, Field(description="magnitude of the loss, e.g. 20 for a 20% drawdown")],
    period_days: Annotated[int, Field(description="how many days you're considering recovering it in")],
) -> RecoveryMathResult:
    """Eternal_Master_Build_Plan.md Section 7.2's math correction, made
    callable: a loss of X% requires a LARGER percentage gain than X to
    break even, because the gain is computed on a smaller base. Call this
    before ever configuring an aggressive catch-up plan, not after."""
    return RecoveryMathResult(**data.get_engine().check_recovery_math(drawdown_pct, period_days))


@mcp.tool(
    name="trading_get_paper_positions",
    annotations={"title": "List current paper-trading positions", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def trading_get_paper_positions() -> List[PaperPositionModel]:
    return [PaperPositionModel(**p) for p in data.get_engine().get_paper_positions()]


@mcp.tool(
    name="trading_get_paper_margins",
    annotations={"title": "Get paper-trading cash balance", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def trading_get_paper_margins() -> PaperMarginsModel:
    return PaperMarginsModel(**data.get_engine().get_paper_margins())


@mcp.tool(
    name="trading_daily_reauth_status",
    annotations={"title": "Check today's Kite Connect login status", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def trading_daily_reauth_status() -> DailyReauthStatusModel:
    """Whether today's Kite Connect access token is still valid — see
    trading_risk_engine/broker/daily_reauth.py. Reports 'configured: false' rather
    than an error if live trading credentials were never set at all."""
    return DailyReauthStatusModel(**data.get_engine().daily_reauth_status())


@mcp.tool(
    name="trading_ip_change_history",
    annotations={"title": "View recorded IP change history", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def trading_ip_change_history(limit: Annotated[int, Field(default=20)] = 20) -> List[IPChangeRecordModel]:
    return [IPChangeRecordModel(**r) for r in data.get_engine().ip_change_history(limit)]


@mcp.tool(
    name="trading_place_paper_order",
    annotations={"title": "Place a simulated (paper) order", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def trading_place_paper_order(
    symbol: Annotated[str, Field(description="e.g. 'RELIANCE.NS'")],
    side: Annotated[str, Field(description="'BUY' or 'SELL'")],
    quantity: Annotated[int, Field(gt=0)],
    order_type: Annotated[str, Field(description="'MARKET' or 'LIMIT'")] = "MARKET",
    limit_price: Optional[float] = None,
) -> OrderResult:
    """No real money, no real exchange — a genuine accounting entry
    against trading_risk_engine/broker/paper_broker.py's persisted cash/position
    ledger, priced against the current (simulated, in this build) quote."""
    try:
        result = data.get_engine().place_paper_order(symbol, side, quantity, order_type, limit_price)
        return _order_dict_to_result(result)
    except data.TradingEngineError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="trading_run_paper_trading_day",
    annotations={"title": "Advance the paper-trading simulation by one day", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def trading_run_paper_trading_day(
    symbols: Annotated[List[str], Field(description="e.g. ['RELIANCE.NS', 'TCS.NS']")],
    trading_date: Annotated[str, Field(description="ISO date; must be strictly after the last date processed")],
) -> PaperTradingDayResult:
    """One real step of Eternal_Master_Build_Plan.md Section 8's
    'paper-trading for several weeks minimum' — decides today's action
    from today's close (to be filled tomorrow) and fills whatever was
    decided yesterday, using today's open. Call this once per trading
    day, in order; see trading_risk_engine/paper_trader.py's module docstring."""
    try:
        result = data.get_engine().run_paper_trading_day(symbols, trading_date)
        return PaperTradingDayResult(**result)
    except (data.TradingEngineError, ValueError) as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="trading_record_ip_change",
    annotations={"title": "Record the current order-placement host's IP", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def trading_record_ip_change(ip_address: str) -> IPChangeResult:
    """Tracking only — see trading_risk_engine/regulatory.py's IPChangeTracker
    docstring for why this never silently blocks an order on its own."""
    return IPChangeResult(**data.get_engine().record_ip_change(ip_address))


@mcp.tool(
    name="trading_place_live_order",
    annotations={"title": "Place a REAL order via Kite Connect", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
)
async def trading_place_live_order(
    symbol: Annotated[str, Field(description="e.g. 'RELIANCE.NS'")],
    side: Annotated[str, Field(description="'BUY' or 'SELL'")],
    quantity: Annotated[int, Field(gt=0)],
    order_type: Annotated[str, Field(description="'MARKET' or 'LIMIT' — LIMIT strongly preferred")] = "LIMIT",
    limit_price: Optional[float] = None,
    market_protection_pct: Optional[float] = None,
) -> OrderResult:
    """Real money, real exchange, real irreversible consequence. This
    tool does not check whether it's ALLOWED to run — by the time this
    function body executes, trading_risk_engine.gateway.Phase6Gateway has already
    required: an ACTIVE (non-quarantined) QAP-P session, the top trust
    tier, a non-voice confirmation, and every regulatory gate in
    trading_risk_engine/regulatory.py (fixed-IP host role, valid daily Kite login,
    order rate limit, and — for a MARKET order — a real
    market_protection_pct). SENSITIVE tier; see
    trading_risk_engine/registry_ext.py."""
    try:
        result = data.get_engine().place_live_order(symbol, side, quantity, order_type, limit_price,
                                                     market_protection_pct)
        return _order_dict_to_result(result)
    except data.TradingEngineError as e:
        raise ValueError(str(e)) from e


# -- F&O tools (Index Options + Index Futures) ------------------------------
# Tiers assigned in trading_risk_engine/registry_ext.py: the six read-only tools below
# are READ_ONLY; the three paper-order tools are WRITE; the two live-order
# tools are SENSITIVE (gated by trading_risk_engine/gateway.py's LIVE_ORDER_TOOL_NAMES,
# same top-trust-tier + regulatory chain as trading_place_live_order).

_UNDERLYING_DESC = "Index underlying — 'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', or 'SENSEX'"
_EXPIRY_CHOICE_DESC = "'WEEKLY' or 'MONTHLY' (NIFTY/SENSEX only have weekly; others are monthly-only)"


@mcp.tool(
    name="fo_get_option_chain",
    annotations={"title": "Get a simulated index option chain", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": False, "openWorldHint": True},
)
async def fo_get_option_chain(
    underlying_symbol: Annotated[str, Field(description=_UNDERLYING_DESC)],
    expiry_choice: Annotated[str, Field(description=_EXPIRY_CHOICE_DESC)] = "WEEKLY",
    num_strikes_each_side: Annotated[int, Field(gt=0, le=40)] = 10,
) -> OptionChainResult:
    """Full CE+PE chain around the money, priced from Black-Scholes-Merton
    (trading_risk_engine/options_pricing.py) against a SIMULATED underlying spot (see
    trading_risk_engine/instruments/fo_data_provider.py) — not a real Kite quote."""
    try:
        return OptionChainResult(**data.get_fo_engine().get_option_chain(
            underlying_symbol, expiry_choice, num_strikes_each_side))
    except data.TradingEngineError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="fo_get_signal",
    annotations={"title": "Get the rule-based signal for an index underlying", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def fo_get_signal(
    underlying_symbol: Annotated[str, Field(description=_UNDERLYING_DESC)],
    lookback_days: Annotated[int, Field(gt=0)] = 400,
) -> SignalResult:
    """Runs the EXACT SAME rule engine trading_get_signal uses
    (trading_risk_engine/signals.py), against the index's own simulated price path —
    the signal itself doesn't know or care whether it's looking at a
    stock or an index (see fo_backtester.py's module docstring)."""
    try:
        return SignalResult(**data.get_fo_engine().get_signal(underlying_symbol, lookback_days))
    except data.TradingEngineError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="fo_run_options_backtest",
    annotations={"title": "Backtest an index options strategy", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def fo_run_options_backtest(
    underlying_symbol: Annotated[str, Field(description=_UNDERLYING_DESC)],
    strategy_style: Annotated[str, Field(
        description="'DIRECTIONAL_LONG' (buy calls/puts, bounded risk) or "
                    "'INCOME_SHORT' (sell puts/calls, collects theta, unbounded-ish risk)")],
    expiry_choice: Annotated[str, Field(description=_EXPIRY_CHOICE_DESC)] = "WEEKLY",
    lookback_days: Annotated[int, Field(gt=1)] = 250,
    starting_cash: Annotated[float, Field(gt=0)] = 1_000_000.0,
) -> FOBacktestResult:
    """No-lookahead event-driven backtest (trading_risk_engine/instruments/fo_backtester.py):
    signal decided from day T's close, position priced and filled at day
    T+1's underlying OPEN, marked to market daily at CLOSE. Reuses the
    same directional signal engine as fo_get_signal; only the options
    P&L mechanics (premium decay, margin) are new."""
    try:
        return FOBacktestResult(**data.get_fo_engine().run_options_backtest(
            underlying_symbol, strategy_style, expiry_choice, lookback_days, starting_cash))
    except data.TradingEngineError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="fo_run_futures_backtest",
    annotations={"title": "Backtest an index futures strategy", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def fo_run_futures_backtest(
    underlying_symbol: Annotated[str, Field(description=_UNDERLYING_DESC)],
    lookback_days: Annotated[int, Field(gt=1)] = 250,
    starting_cash: Annotated[float, Field(gt=0)] = 1_000_000.0,
) -> FOBacktestResult:
    """Same no-lookahead discipline as fo_run_options_backtest. Index
    futures are always a monthly cycle (never weekly, for any index —
    see calendar.py), so there is no expiry_choice here."""
    try:
        return FOBacktestResult(**data.get_fo_engine().run_futures_backtest(
            underlying_symbol, lookback_days, starting_cash))
    except data.TradingEngineError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="fo_get_paper_positions",
    annotations={"title": "List current F&O paper-trading positions", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def fo_get_paper_positions() -> List[FOPaperPositionModel]:
    return [FOPaperPositionModel(**p) for p in data.get_fo_engine().get_paper_positions()]


@mcp.tool(
    name="fo_get_paper_margins",
    annotations={"title": "Get F&O paper-trading cash/margin balances", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def fo_get_paper_margins() -> FOPaperMarginsModel:
    return FOPaperMarginsModel(**data.get_fo_engine().get_paper_margins())


@mcp.tool(
    name="fo_place_paper_option_order",
    annotations={"title": "Place a simulated (paper) index option order", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def fo_place_paper_option_order(
    underlying_symbol: Annotated[str, Field(description=_UNDERLYING_DESC)],
    strike: Annotated[float, Field(gt=0)],
    option_type: Annotated[str, Field(description="'CE' (call) or 'PE' (put)")],
    side: Annotated[str, Field(description="'LONG' (buy) or 'SHORT' (sell/write)")],
    num_lots: Annotated[int, Field(gt=0)],
    expiry_choice: Annotated[str, Field(description=_EXPIRY_CHOICE_DESC)] = "WEEKLY",
) -> FOPaperOrderResult:
    """A manual override, exactly like trading_place_paper_order: does
    precisely the lot count given, priced against the current SIMULATED
    theoretical premium — no auto-sizing (FORiskManager's sizing is used
    by the backtester, not here). Stop/target are still set from
    FOConfig's standard percentage rules so the position isn't left
    without real risk levels."""
    try:
        result = data.get_fo_engine().place_paper_option_order(
            underlying_symbol, strike, expiry_choice, option_type, side, num_lots)
        return FOPaperOrderResult(**result)
    except data.TradingEngineError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="fo_place_paper_future_order",
    annotations={"title": "Place a simulated (paper) index future order", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def fo_place_paper_future_order(
    underlying_symbol: Annotated[str, Field(description=_UNDERLYING_DESC)],
    side: Annotated[str, Field(description="'LONG' (buy) or 'SHORT' (sell)")],
    num_lots: Annotated[int, Field(gt=0)],
) -> FOPaperOrderResult:
    """Manual-override paper future order at the next monthly expiry
    (futures never have a weekly cycle). Stop/target come from the
    underlying's current ATR times FOConfig's futures multipliers, same
    as the automated futures backtest uses."""
    try:
        result = data.get_fo_engine().place_paper_future_order(underlying_symbol, side, num_lots)
        return FOPaperOrderResult(**result)
    except data.TradingEngineError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="fo_close_paper_position",
    annotations={"title": "Close an open F&O paper position", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": False},
)
async def fo_close_paper_position(
    position_id: Annotated[str, Field(description="the position_id from fo_get_paper_positions")],
) -> FOClosePositionResult:
    """Marks the position to today's SIMULATED theoretical price and
    realizes P&L against the paper cash/margin ledger. Irreversible for
    that position (there is no re-open)."""
    try:
        return FOClosePositionResult(**data.get_fo_engine().close_paper_position(position_id))
    except data.TradingEngineError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="fo_place_live_option_order",
    annotations={"title": "Place a REAL index option order via Kite Connect", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
)
async def fo_place_live_option_order(
    underlying_symbol: Annotated[str, Field(description=_UNDERLYING_DESC)],
    strike: Annotated[float, Field(gt=0)],
    option_type: Annotated[str, Field(description="'CE' (call) or 'PE' (put)")],
    side: Annotated[str, Field(description="'LONG' (buy to open) or 'SHORT' (sell to open)")],
    num_lots: Annotated[int, Field(gt=0)],
    expiry_choice: Annotated[str, Field(description=_EXPIRY_CHOICE_DESC)] = "WEEKLY",
    order_type: Annotated[str, Field(description="'MARKET' or 'LIMIT' — LIMIT strongly preferred")] = "LIMIT",
    limit_price: Optional[float] = None,
    market_protection_pct: Annotated[Optional[float], Field(description="required (>0) for MARKET orders; ignored for LIMIT")] = None,
) -> FOLiveOrderResult:
    """Real money, real exchange, real irreversible consequence — same
    gate chain as trading_place_live_order (active non-quarantined
    session, top trust tier, non-voice confirmation, fixed-IP host role,
    valid daily Kite login, order rate limit), enforced by
    trading_risk_engine.gateway.Phase6Gateway BEFORE this function body runs (see
    LIVE_ORDER_TOOL_NAMES there). The tradingsymbol is resolved from
    Kite's own live instrument list, never pattern-constructed — see
    trading_risk_engine/instruments/fo_kite_connect_broker.py. SENSITIVE tier."""
    try:
        result = data.get_fo_engine().place_live_option_order(
            underlying_symbol, strike, expiry_choice, option_type, side, num_lots, order_type, limit_price, market_protection_pct)
        return FOLiveOrderResult(**result)
    except data.TradingEngineError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="fo_place_live_future_order",
    annotations={"title": "Place a REAL index future order via Kite Connect", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
)
async def fo_place_live_future_order(
    underlying_symbol: Annotated[str, Field(description=_UNDERLYING_DESC)],
    side: Annotated[str, Field(description="'LONG' (buy to open) or 'SHORT' (sell to open)")],
    num_lots: Annotated[int, Field(gt=0)],
    order_type: Annotated[str, Field(description="'MARKET' or 'LIMIT' — LIMIT strongly preferred")] = "LIMIT",
    limit_price: Optional[float] = None,
    market_protection_pct: Annotated[Optional[float], Field(description="required (>0) for MARKET orders; ignored for LIMIT")] = None,
) -> FOLiveOrderResult:
    """Same real-consequence gate chain as fo_place_live_option_order,
    at the next monthly futures expiry. SENSITIVE tier."""
    try:
        result = data.get_fo_engine().place_live_future_order(
            underlying_symbol, side, num_lots, order_type, limit_price, market_protection_pct)
        return FOLiveOrderResult(**result)
    except data.TradingEngineError as e:
        raise ValueError(str(e)) from e


if __name__ == "__main__":
    mcp.run()
