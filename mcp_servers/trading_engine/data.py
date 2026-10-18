"""
Trading engine — business logic for Phase 6's MCP server.

Same separation laptop_agent/data.py, remote_control/data.py, and
home_assistant/data.py already established: this module holds the real
logic and a lazily-constructed singleton (`get_provider()`); server.py is
a thin FastMCP wrapper that never makes a decision on its own.

None of these functions decide on their own whether they're ALLOWED to
run — that's trading_risk_engine.registry_ext (sensitivity tier), voice_consent_gateway.consent and
voice_consent_gateway.voice_gate (reused unmodified, exactly like Phase 5's tools), and
device_control_gateway's kill-switch coverage (via Phase5Gateway.grant_tool_permission,
inherited automatically — see trading_risk_engine/gateway.py's module docstring for
why no NEW code is needed to get that specific protection), all composed
in trading_risk_engine.gateway.Phase6Gateway. This module answers "what does the
action actually do," never "should this action be allowed right now."
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import date as date_type
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from trading_risk_engine import indicators as ind
from trading_risk_engine import performance as perf
from trading_risk_engine.backtester import Backtester
from trading_risk_engine.broker.daily_reauth import DailyReauthManager
from trading_risk_engine.broker.kite_connect import KiteConnectBroker
from trading_risk_engine.broker.paper_broker import PaperBroker
from trading_risk_engine.config import Config
from trading_risk_engine.data_provider import MarketDataProvider, MarketDataUnavailableError, SimulatedMarketDataProvider
from trading_risk_engine.ledger_bridge import Ledger
from trading_risk_engine.models import OrderSide, OrderType
from trading_risk_engine.paper_trader import PaperTrader
from trading_risk_engine.regulatory import (
    DailyReauthGate,
    IPChangeTracker,
    LiveTradingGate,
    OrderRateLimiter,
    StaticIPHostGate,
)
from trading_risk_engine.risk import RiskManager


# -- F&O extension (Index Options + Index Futures) --------------------------
# Composition, not modification: every class below is a NEW, separate
# engine that wraps/reuses pieces of the equity TradingEngine above
# (its shared Ledger, Config.data_dir, DailyReauthManager, and — per
# fo_backtester.py's own "instrument adapter" principle — its exact
# SignalEngine instance) rather than bolting F&O branches onto the
# equity methods themselves.
from trading_risk_engine.instruments.calendar import ExpiryResolver
from trading_risk_engine.instruments.contracts import FutureContract, OptionContract, OptionType
from trading_risk_engine.instruments.fo_backtester import ExpiryChoice, FOBacktester, OptionStrategyStyle
from trading_risk_engine.instruments.fo_config import FOConfig
from trading_risk_engine.instruments.fo_data_provider import FOMarketDataProvider, OptionQuote
from trading_risk_engine.instruments.fo_kite_connect_broker import FOKiteConnectBroker
from trading_risk_engine.instruments.fo_paper_broker import FOPaperBroker
from trading_risk_engine.instruments.fo_performance import build_fo_performance
from trading_risk_engine.instruments.fo_risk import FORiskManager
from trading_risk_engine.instruments.margin import MarginConfig, future_margin_required, option_margin_required
from trading_risk_engine.instruments.underlyings import get_underlying




class TradingEngineError(Exception):
    pass


@dataclass
class TradingEngine:
    config: Config
    data_provider: MarketDataProvider
    risk_manager: RiskManager
    paper_broker: PaperBroker
    paper_trader: PaperTrader
    ledger: Ledger
    ip_tracker: IPChangeTracker
    live_trading_gate: Optional[LiveTradingGate]
    live_broker: Optional[KiteConnectBroker]
    daily_reauth_manager: Optional[DailyReauthManager]

    # -- Read-only capabilities --------------------------------------------------

    def get_quote(self, symbol: str) -> dict:
        try:
            return self.data_provider.get_latest_quote(symbol)
        except MarketDataUnavailableError as e:
            raise TradingEngineError(str(e)) from e

    def get_signal(self, symbol: str, lookback_days: int = 400) -> dict:
        end = date_type.today()
        start = end.fromordinal(end.toordinal() - lookback_days)
        try:
            bars = self.data_provider.get_daily_bars(symbol, start, end)
        except MarketDataUnavailableError as e:
            raise TradingEngineError(str(e)) from e
        indicator_df = ind.compute_all(
            bars, atr_period=self.config.atr_period, rsi_period=self.config.rsi_period,
            bb_period=self.config.bb_period, bb_num_std=self.config.bb_num_std,
            sma_trend_period=self.config.sma_trend_period,
        )
        signal = self.paper_trader.signal_engine.generate(symbol, indicator_df)
        self.ledger.append("trading_signal_requested", signal.as_ledger_payload())
        return {
            "symbol": signal.symbol, "date": signal.date, "direction": signal.direction.value,
            "score": signal.score, "reasons": signal.reasons,
        }

    def run_backtest(self, symbols: List[str], start_date: str, end_date: str,
                     starting_cash: float = 100_000.0) -> dict:
        start = date_type.fromisoformat(start_date)
        end = date_type.fromisoformat(end_date)
        symbol_data = {}
        for symbol in symbols:
            try:
                symbol_data[symbol] = self.data_provider.get_daily_bars(symbol, start, end)
            except MarketDataUnavailableError as e:
                raise TradingEngineError(f"{symbol}: {e}") from e

        backtester = Backtester(self.config)
        try:
            report = backtester.run(symbol_data, starting_cash=starting_cash)
        except ValueError as e:
            raise TradingEngineError(str(e)) from e

        self.ledger.append("trading_backtest_run", {
            "symbols": symbols, "start_date": start_date, "end_date": end_date,
            "num_trades": report.performance.num_trades,
            "total_return_pct": round(report.performance.total_return_pct, 4),
            "sharpe_ratio": round(report.performance.sharpe_ratio, 4),
            "max_drawdown_pct": round(report.performance.max_drawdown_pct, 4),
        })
        return {
            "symbols": report.symbols, "start_date": report.start_date, "end_date": report.end_date,
            "performance": asdict(report.performance),
            "num_trades": len(report.trades),
            "circuit_breaker_triggers": report.circuit_breaker_triggers,
            "trades": [asdict(t) | {"exit_reason": t.exit_reason.value} for t in report.trades][:200],
        }

    def get_paper_positions(self) -> List[dict]:
        return [asdict(p) for p in self.paper_broker.get_positions()]

    def get_paper_margins(self) -> dict:
        return self.paper_broker.get_margins()

    def check_recovery_math(self, drawdown_pct: float, period_days: int) -> dict:
        drawdown_fraction = abs(drawdown_pct) / 100.0
        needed = perf.recovery_return_needed(min(drawdown_fraction, 0.999999))
        unrealistic = perf.is_recovery_target_unrealistic(needed * 100.0, period_days)
        return {
            "drawdown_pct": drawdown_pct, "period_days": period_days,
            "return_needed_to_recover_pct": round(needed * 100.0, 4),
            "is_unrealistic_for_this_period": unrealistic,
            "note": ("Eternal_Master_Build_Plan.md Section 7.2: recovering a loss requires a LARGER "
                    "percentage gain than the loss itself, because the gain is computed on a smaller "
                    "base. This is arithmetic, not pessimism."),
        }

    def daily_reauth_status(self) -> dict:
        if self.daily_reauth_manager is None:
            return {"configured": False, "note": "KITE_API_KEY/KITE_API_SECRET not set — live trading is not configured"}
        status = self.daily_reauth_manager.token_status()
        return {"configured": True, **asdict(status)}

    def ip_change_history(self, limit: int = 20) -> List[dict]:
        return self.ip_tracker.history(limit)

    # -- Write capabilities (paper) ------------------------------------------------

    def place_paper_order(self, symbol: str, side: str, quantity: int, order_type: str,
                          limit_price: Optional[float] = None) -> dict:
        """A MANUAL override, priced against the current quote — this
        bypasses the risk-managed sizing/stop-placement flow entirely
        (there is no "how many shares should I buy" or "where's my stop"
        decision here, only "do exactly this"), so a position opened this
        way carries stop_loss_price=0.0/take_profit_price=0.0 until/unless
        something else calls PaperBroker.set_risk_levels for it. That is
        correct for a manual override and very different from
        run_paper_trading_day's automated flow, which always anchors a
        real ATR-based stop/target at fill time — the two are not meant
        to produce the same kind of position."""
        try:
            quote = self.data_provider.get_latest_quote(symbol)
            reference_price = quote["price"]
        except MarketDataUnavailableError as e:
            raise TradingEngineError(f"cannot price this paper order: {e}") from e

        order = self.paper_broker.place_order(
            symbol, OrderSide(side.upper()), quantity, OrderType(order_type.upper()),
            limit_price=limit_price, reference_price=reference_price, tag="mcp_manual_paper_order",
        )
        self.ledger.append("trading_paper_order_placed", {
            "symbol": symbol, "side": side, "quantity": quantity, "order_type": order_type,
            "status": order.status.value, "fill_price": order.fill_price, "reject_reason": order.reject_reason,
        })
        return asdict(order) | {"side": order.side.value, "order_type": order.order_type.value, "status": order.status.value}

    def run_paper_trading_day(self, symbols: List[str], trading_date: str) -> dict:
        result = self.paper_trader.run_trading_day(symbols, date_type.fromisoformat(trading_date))
        return {
            "date": result.date, "fills": result.fills, "new_pending_decisions": result.new_pending_decisions,
            "circuit_breaker": result.circuit_breaker, "equity": result.equity, "cash": result.cash,
            "positions_value": result.positions_value,
        }

    def record_ip_change(self, ip_address: str) -> dict:
        result = self.ip_tracker.record_ip(ip_address)
        self.ledger.append("trading_ip_change_recorded", {"ip_address": ip_address, "allowed": result.allowed,
                                                           "reason": result.reason})
        return {"allowed": result.allowed, "reason": result.reason}

    # -- Live trading (SENSITIVE — trading_risk_engine.gateway.Phase6Gateway gates entry to this) --

    def place_live_order(self, symbol: str, side: str, quantity: int, order_type: str,
                         limit_price: Optional[float] = None,
                         market_protection_pct: Optional[float] = None) -> dict:
        """This method assumes the caller (trading_risk_engine.gateway.Phase6Gateway)
        has ALREADY cleared the top-trust-tier check and
        LiveTradingGate.check_before_order — it does not re-check the
        gates itself, exactly the same division of responsibility
        laptop_agent's tools use (the MCP tool does the action; the
        gateway decided whether it was allowed to). It DOES still refuse
        outright if live trading was never configured at all, since that
        is a "this cannot possibly work" state, not a gate decision."""
        if self.live_broker is None:
            raise TradingEngineError(
                "live trading is not configured on this process (KITE_API_KEY/KITE_API_SECRET not set, "
                "or PHASE6_LIVE_TRADING_ENABLED != '1') — see this phase's README before setting these"
            )
        order = self.live_broker.place_order(
            symbol, OrderSide(side.upper()), quantity, OrderType(order_type.upper()),
            limit_price=limit_price, tag="mcp_live_order", market_protection_pct=market_protection_pct,
        )
        self.ledger.append("trading_LIVE_order_placed", {
            "symbol": symbol, "side": side, "quantity": quantity, "order_type": order_type,
            "status": order.status.value, "broker_order_id": order.broker_order_id,
            "reject_reason": order.reject_reason,
        })
        return asdict(order) | {"side": order.side.value, "order_type": order.order_type.value, "status": order.status.value}


_engine: Optional[TradingEngine] = None


def _build_live_trading_components(config: Config):
    api_key = os.environ.get("KITE_API_KEY", "")
    api_secret = os.environ.get("KITE_API_SECRET", "")
    if not api_key or not api_secret:
        return None, None, None

    reauth_manager = DailyReauthManager(
        config.daily_reauth_db_path, api_key, api_secret,
        cutover_hour_ist=config.daily_token_cutover_hour_ist,
        cutover_minute_ist=config.daily_token_cutover_minute_ist,
    )
    live_broker = KiteConnectBroker(api_key, reauth_manager)
    live_trading_gate = LiveTradingGate(
        config,
        static_ip_gate=StaticIPHostGate(config),
        daily_reauth_gate=DailyReauthGate(reauth_manager),
        rate_limiter=OrderRateLimiter(config.max_orders_per_second),
    )
    return live_broker, reauth_manager, live_trading_gate


def get_engine() -> TradingEngine:
    global _engine
    if _engine is None:
        config = Config()
        data_provider = SimulatedMarketDataProvider(seed=42)
        risk_manager = RiskManager(config)
        paper_broker = PaperBroker(
            config.paper_broker_db_path, commission_pct=config.commission_pct,
            commission_flat=config.commission_flat, slippage_bps=config.slippage_bps,
        )
        ledger = Ledger(config.ledger_db_path)
        paper_trader = PaperTrader(config, data_provider=data_provider, risk_manager=risk_manager,
                                   broker=paper_broker, ledger=ledger)
        ip_tracker = IPChangeTracker(config.ip_history_db_path, config.max_ip_changes_per_week)
        live_broker, reauth_manager, live_trading_gate = _build_live_trading_components(config)
        _engine = TradingEngine(
            config=config, data_provider=data_provider, risk_manager=risk_manager,
            paper_broker=paper_broker, paper_trader=paper_trader, ledger=ledger, ip_tracker=ip_tracker,
            live_trading_gate=live_trading_gate, live_broker=live_broker, daily_reauth_manager=reauth_manager,
        )
    return _engine


def reset_engine_for_tests() -> None:
    global _engine
    _engine = None


# ============================================================================
# F&O extension — Index Options + Index Futures (Phase 6 Extended)
# ============================================================================


class FOContractDict(dict):
    """Marker subclass only — see _contract_to_dict. Kept as a plain
    dict so it serializes over MCP without any custom encoder."""


def _contract_to_dict(contract) -> Dict[str, Any]:
    """OptionContract/FutureContract -> a JSON-safe dict. display_symbol
    is included for logging/display ONLY (contracts.py's own docstring:
    never sent to a real broker as the tradingsymbol)."""
    if isinstance(contract, OptionContract):
        return {
            "underlying_symbol": contract.underlying_symbol, "kind": "OPTION",
            "strike": contract.strike, "expiry": contract.expiry.isoformat(),
            "option_type": contract.option_type.value, "display_symbol": contract.display_symbol,
        }
    return {
        "underlying_symbol": contract.underlying_symbol, "kind": "FUTURE",
        "strike": None, "expiry": contract.expiry.isoformat(),
        "option_type": None, "display_symbol": contract.display_symbol,
    }


def _quote_to_dict(quote: OptionQuote) -> Dict[str, Any]:
    return {
        "strike": quote.contract.strike, "option_type": quote.contract.option_type.value,
        "theoretical_price": round(float(quote.theoretical_price), 4),
        "implied_volatility": round(float(quote.implied_volatility), 6),
        "delta": round(float(quote.greeks.delta), 6), "gamma": round(float(quote.greeks.gamma), 8),
        "theta_per_day": round(float(quote.greeks.theta_per_day), 6),
        "vega_per_pct": round(float(quote.greeks.vega_per_pct), 6),
        "rho_per_pct": round(float(quote.greeks.rho_per_pct), 6),
    }


def _fo_trade_to_dict(trade) -> Dict[str, Any]:
    return {
        "contract": _contract_to_dict(trade.contract), "side": trade.side, "num_lots": trade.num_lots,
        "entry_date": trade.entry_date, "exit_date": trade.exit_date,
        "entry_price": round(float(trade.entry_price), 4), "exit_price": round(float(trade.exit_price), 4),
        "exit_reason": trade.exit_reason, "margin_required": round(float(trade.margin_required), 2),
        "risk_amount": round(float(trade.risk_amount), 2), "pnl": round(float(trade.pnl), 2),
        "r_multiple": round(float(trade.r_multiple), 4),
    }


def _fo_backtest_summary(trades: List[Any]) -> Dict[str, Any]:
    """Simple, directly-computed summary stats — NOT a port of
    trading_risk_engine/performance.py (that module's PerformanceReport assumes
    share-quantity equity trades; F&O trades are lot-based with their
    own margin/risk fields, so a real port would need its own dedicated
    module, out of scope for this build). Good enough to answer "did
    this backtest work" without pretending to be the equity report."""
    num_trades = len(trades)
    winners = [t for t in trades if t.pnl > 0]
    losers = [t for t in trades if t.pnl <= 0]
    win_rate_pct = (len(winners) / num_trades * 100.0) if num_trades > 0 else 0.0
    return {
        "num_trades": num_trades, "num_winning_trades": len(winners), "num_losing_trades": len(losers),
        "win_rate_pct": round(win_rate_pct, 4),
        "total_pnl": round(float(sum(t.pnl for t in trades)), 2),
        "avg_win": round(float(sum(t.pnl for t in winners) / len(winners)), 2) if winners else 0.0,
        "avg_loss": round(float(sum(t.pnl for t in losers) / len(losers)), 2) if losers else 0.0,
    }


def _fo_backtest_report_to_dict(report, risk_free_pct: float = 0.065) -> Dict[str, Any]:
    # Same "cap the payload" convention as equity's run_backtest
    # ([:200] on trades) — MCP JSON responses should stay bounded
    # regardless of how many bars a backtest covers.
    return {
        "underlying_symbol": report.underlying_symbol, "strategy_style": report.strategy_style,
        "starting_equity": report.starting_equity, "ending_equity": report.ending_equity,
        "total_return_pct": round(float(report.total_return_pct), 4),
        "summary": _fo_backtest_summary(report.trades),
        "performance": build_fo_performance(report.equity_curve, report.trades, annual_risk_free_pct=risk_free_pct),
        "trades": [_fo_trade_to_dict(t) for t in report.trades][:200],
        "equity_curve": [asdict(p) for p in report.equity_curve][-200:],
    }


def _validate_side(side: str) -> str:
    side_upper = (side or "").upper()
    if side_upper not in ("LONG", "SHORT"):
        raise TradingEngineError(f"side must be 'LONG' or 'SHORT', got '{side}'")
    return side_upper


def _resolve_expiry(resolver: ExpiryResolver, underlying, expiry_choice: str, as_of: date_type) -> date_type:
    choice = (expiry_choice or "WEEKLY").upper()
    try:
        if choice == "WEEKLY":
            return resolver.next_weekly_expiry(underlying, as_of)
        if choice == "MONTHLY":
            return resolver.next_monthly_expiry(underlying, as_of)
    except ValueError as e:
        raise TradingEngineError(str(e)) from e
    raise TradingEngineError(f"expiry_choice must be 'WEEKLY' or 'MONTHLY', got '{expiry_choice}'")


@dataclass
class FOTradingEngine:
    fo_config: FOConfig
    margin_config: MarginConfig
    fo_data_provider: FOMarketDataProvider
    fo_risk_manager: FORiskManager
    fo_paper_broker: FOPaperBroker
    fo_backtester: FOBacktester
    expiry_resolver: ExpiryResolver
    equity_engine: TradingEngine  # composition — shared ledger/config/daily-reauth/signal-engine, see docstring above
    live_fo_broker: Optional[FOKiteConnectBroker]

    # -- internal helpers -----------------------------------------------------

    def _latest_spot(self, underlying_symbol: str, as_of: date_type) -> float:
        start = as_of - timedelta(days=10)
        try:
            bars = self.fo_data_provider.get_underlying_daily_bars(underlying_symbol, start, as_of)
        except MarketDataUnavailableError as e:
            raise TradingEngineError(str(e)) from e
        return float(bars["close"].iloc[-1])

    def _latest_atr(self, underlying_symbol: str, as_of: date_type) -> float:
        start = as_of - timedelta(days=max(60, self.equity_engine.config.atr_period * 3))
        try:
            bars = self.fo_data_provider.get_underlying_daily_bars(underlying_symbol, start, as_of)
        except MarketDataUnavailableError as e:
            raise TradingEngineError(str(e)) from e
        indicator_df = ind.compute_all(
            bars, atr_period=self.equity_engine.config.atr_period, rsi_period=self.equity_engine.config.rsi_period,
            bb_period=self.equity_engine.config.bb_period, sma_trend_period=self.equity_engine.config.sma_trend_period,
        )
        atr_series = indicator_df["atr"].dropna()
        if len(atr_series) == 0:
            raise TradingEngineError(f"not enough price history to compute ATR for {underlying_symbol}")
        return float(atr_series.iloc[-1])

    def _get_underlying_or_raise(self, underlying_symbol: str):
        try:
            return get_underlying(underlying_symbol)
        except ValueError as e:
            raise TradingEngineError(str(e)) from e

    # -- Read-only capabilities -------------------------------------------------

    def get_option_chain(self, underlying_symbol: str, expiry_choice: str = "WEEKLY",
                         num_strikes_each_side: int = 10) -> dict:
        underlying = self._get_underlying_or_raise(underlying_symbol)
        as_of = date_type.today()
        expiry = _resolve_expiry(self.expiry_resolver, underlying, expiry_choice, as_of)
        spot = self._latest_spot(underlying_symbol, as_of)
        quotes = self.fo_data_provider.get_option_chain(underlying_symbol, as_of, spot, expiry, num_strikes_each_side)
        self.equity_engine.ledger.append("fo_option_chain_requested", {
            "underlying_symbol": underlying_symbol, "expiry": expiry.isoformat(), "spot": spot,
            "num_quotes": len(quotes),
        })
        return {
            "underlying_symbol": underlying_symbol, "spot": round(spot, 4), "expiry": expiry.isoformat(),
            "as_of": as_of.isoformat(), "quotes": [_quote_to_dict(q) for q in quotes],
        }

    def get_signal(self, underlying_symbol: str, lookback_days: int = 400) -> dict:
        self._get_underlying_or_raise(underlying_symbol)
        end = date_type.today()
        start = end - timedelta(days=lookback_days)
        try:
            bars = self.fo_data_provider.get_underlying_daily_bars(underlying_symbol, start, end)
        except MarketDataUnavailableError as e:
            raise TradingEngineError(str(e)) from e
        indicator_df = ind.compute_all(
            bars, atr_period=self.equity_engine.config.atr_period, rsi_period=self.equity_engine.config.rsi_period,
            bb_period=self.equity_engine.config.bb_period, sma_trend_period=self.equity_engine.config.sma_trend_period,
        )
        # Reuses the EQUITY engine's own SignalEngine instance, unmodified —
        # per fo_backtester.py's "instrument adapter" principle: the index's
        # own simulated price path runs through the exact same signal logic.
        signal = self.equity_engine.paper_trader.signal_engine.generate(underlying_symbol, indicator_df)
        self.equity_engine.ledger.append("fo_signal_requested", signal.as_ledger_payload())
        return {
            "symbol": signal.symbol, "date": signal.date, "direction": signal.direction.value,
            "score": signal.score, "reasons": signal.reasons,
        }

    def run_options_backtest(self, underlying_symbol: str, strategy_style: str, expiry_choice: str = "WEEKLY",
                             lookback_days: int = 250, starting_cash: float = 1_000_000.0) -> dict:
        self._get_underlying_or_raise(underlying_symbol)
        try:
            style = OptionStrategyStyle(strategy_style.upper())
        except ValueError as e:
            raise TradingEngineError(
                f"strategy_style must be 'DIRECTIONAL_LONG' or 'INCOME_SHORT', got '{strategy_style}'"
            ) from e
        try:
            choice = ExpiryChoice((expiry_choice or "WEEKLY").upper())
        except ValueError as e:
            raise TradingEngineError(f"expiry_choice must be 'WEEKLY' or 'MONTHLY', got '{expiry_choice}'") from e

        end = date_type.today()
        start = end - timedelta(days=lookback_days)
        try:
            bars = self.fo_data_provider.get_underlying_daily_bars(underlying_symbol, start, end)
        except MarketDataUnavailableError as e:
            raise TradingEngineError(str(e)) from e

        try:
            report = self.fo_backtester.run_option_strategy(
                underlying_symbol, bars, self.fo_data_provider, style, choice, starting_cash,
            )
        except ValueError as e:
            raise TradingEngineError(str(e)) from e

        result = _fo_backtest_report_to_dict(report, self.equity_engine.config.annual_risk_free_rate_pct)
        self.equity_engine.ledger.append("fo_options_backtest_run", {
            "underlying_symbol": underlying_symbol, "strategy_style": style.value, "expiry_choice": choice.value,
            "num_trades": result["summary"]["num_trades"], "total_return_pct": result["total_return_pct"],
        })
        return result

    def run_futures_backtest(self, underlying_symbol: str, lookback_days: int = 250,
                             starting_cash: float = 1_000_000.0) -> dict:
        self._get_underlying_or_raise(underlying_symbol)
        end = date_type.today()
        start = end - timedelta(days=lookback_days)
        try:
            bars = self.fo_data_provider.get_underlying_daily_bars(underlying_symbol, start, end)
        except MarketDataUnavailableError as e:
            raise TradingEngineError(str(e)) from e

        try:
            report = self.fo_backtester.run_futures_strategy(underlying_symbol, bars, self.fo_data_provider, starting_cash)
        except ValueError as e:
            raise TradingEngineError(str(e)) from e

        result = _fo_backtest_report_to_dict(report, self.equity_engine.config.annual_risk_free_rate_pct)
        self.equity_engine.ledger.append("fo_futures_backtest_run", {
            "underlying_symbol": underlying_symbol, "num_trades": result["summary"]["num_trades"],
            "total_return_pct": result["total_return_pct"],
        })
        return result

    def get_paper_positions(self) -> List[dict]:
        return self.fo_paper_broker.get_positions()

    def get_paper_margins(self) -> dict:
        return self.fo_paper_broker.get_margins()

    # -- Write capabilities (paper) ----------------------------------------------
    # Manual-override style, same philosophy as equity's place_paper_order:
    # the caller states exactly what to trade (strike/expiry/side/lots);
    # FORiskManager's auto-sizing is a separate concern (used by the
    # backtester), not invoked here. Stop/target are still computed from
    # FOConfig's own percentage rules (never left at a degenerate 0.0,
    # which would be meaningless for a SHORT option's risk).

    def place_paper_option_order(self, underlying_symbol: str, strike: float, expiry_choice: str,
                                 option_type: str, side: str, num_lots: int) -> dict:
        if num_lots <= 0:
            raise TradingEngineError("num_lots must be a positive integer")
        side = _validate_side(side)
        try:
            option_type_enum = OptionType(option_type.upper())
        except ValueError as e:
            raise TradingEngineError(f"option_type must be 'CE' or 'PE', got '{option_type}'") from e
        underlying = self._get_underlying_or_raise(underlying_symbol)
        today = date_type.today()
        expiry = _resolve_expiry(self.expiry_resolver, underlying, expiry_choice, today)
        contract = OptionContract(underlying_symbol=underlying_symbol, strike=float(strike),
                                  expiry=expiry, option_type=option_type_enum)
        spot = self._latest_spot(underlying_symbol, today)
        premium = self.fo_data_provider.price_option(contract, today, spot).theoretical_price
        margin_required = option_margin_required(contract, side, num_lots, premium, spot, self.margin_config)

        if side == "LONG":
            stop_loss_price = premium * (1.0 - self.fo_config.long_option_stop_loss_pct)
            take_profit_price = premium * (1.0 + self.fo_config.long_option_take_profit_pct)
        else:
            stop_loss_price = premium * self.fo_config.short_option_stop_loss_multiple
            take_profit_price = premium * (1.0 - self.fo_config.short_option_take_profit_pct)

        if not self.fo_risk_manager.can_open_new_position(len(self.fo_paper_broker.get_positions())):
            raise TradingEngineError(
                f"max_concurrent_fo_positions ({self.fo_config.max_concurrent_fo_positions}) reached — "
                "close a position before opening another"
            )

        order = self.fo_paper_broker.open_option_position(
            contract, side, num_lots, premium, margin_required, stop_loss_price, take_profit_price, today.isoformat(),
        )
        self.equity_engine.ledger.append("fo_paper_option_order_placed", {
            "underlying_symbol": underlying_symbol, "display_symbol": contract.display_symbol, "side": side,
            "num_lots": num_lots, "premium": round(float(premium), 4), "status": order.status,
            "reject_reason": order.reject_reason,
        })
        return asdict(order)

    def place_paper_future_order(self, underlying_symbol: str, side: str, num_lots: int) -> dict:
        if num_lots <= 0:
            raise TradingEngineError("num_lots must be a positive integer")
        side = _validate_side(side)
        underlying = self._get_underlying_or_raise(underlying_symbol)
        today = date_type.today()
        # Index futures are always monthly-cycle only (never weekly, for
        # any index) — see calendar.py / fo_backtester.py.
        expiry = _resolve_expiry(self.expiry_resolver, underlying, "MONTHLY", today)
        contract = FutureContract(underlying_symbol=underlying_symbol, expiry=expiry)
        spot = self._latest_spot(underlying_symbol, today)
        price = self.fo_data_provider.price_future(contract, today, spot).theoretical_price
        margin_required = future_margin_required(contract, num_lots, price, self.margin_config)

        atr_value = self._latest_atr(underlying_symbol, today)
        stop_distance = atr_value * self.fo_config.futures_atr_stop_multiplier
        target_distance = atr_value * self.fo_config.futures_atr_target_multiplier
        if side == "LONG":
            stop_loss_price, take_profit_price = price - stop_distance, price + target_distance
        else:
            stop_loss_price, take_profit_price = price + stop_distance, price - target_distance

        if not self.fo_risk_manager.can_open_new_position(len(self.fo_paper_broker.get_positions())):
            raise TradingEngineError(
                f"max_concurrent_fo_positions ({self.fo_config.max_concurrent_fo_positions}) reached — "
                "close a position before opening another"
            )

        order = self.fo_paper_broker.open_future_position(
            contract, side, num_lots, price, margin_required, stop_loss_price, take_profit_price, today.isoformat(),
        )
        self.equity_engine.ledger.append("fo_paper_future_order_placed", {
            "underlying_symbol": underlying_symbol, "display_symbol": contract.display_symbol, "side": side,
            "num_lots": num_lots, "price": round(float(price), 4), "status": order.status,
            "reject_reason": order.reject_reason,
        })
        return asdict(order)

    def close_paper_position(self, position_id: str) -> dict:
        positions = self.fo_paper_broker.get_positions()
        row = next((p for p in positions if p["position_id"] == position_id), None)
        if row is None:
            raise TradingEngineError(f"no open F&O paper position with id '{position_id}'")

        today = date_type.today()
        spot = self._latest_spot(row["underlying_symbol"], today)
        if row["kind"] == "OPTION":
            contract = OptionContract(underlying_symbol=row["underlying_symbol"], strike=row["strike"],
                                      expiry=date_type.fromisoformat(row["expiry"]),
                                      option_type=OptionType(row["option_type"]))
            exit_price = self.fo_data_provider.price_option(contract, today, spot).theoretical_price
        else:
            contract = FutureContract(underlying_symbol=row["underlying_symbol"],
                                      expiry=date_type.fromisoformat(row["expiry"]))
            exit_price = self.fo_data_provider.price_future(contract, today, spot).theoretical_price

        pnl = self.fo_paper_broker.close_position(position_id, exit_price)
        self.equity_engine.ledger.append("fo_paper_position_closed", {
            "position_id": position_id, "underlying_symbol": row["underlying_symbol"],
            "exit_price": round(float(exit_price), 4), "pnl": round(float(pnl), 2) if pnl is not None else None,
        })
        return {"position_id": position_id, "display_symbol": contract.display_symbol,
                "exit_price": round(float(exit_price), 4), "pnl": round(float(pnl), 2) if pnl is not None else None}

    # -- Live trading (SENSITIVE — trading_risk_engine.gateway.Phase6Gateway gates entry to this) --
    # Same division of responsibility as equity's place_live_order: this
    # method assumes the gateway ALREADY cleared top-trust-tier +
    # LiveTradingGate for THIS tool name (see gateway.py's
    # LIVE_ORDER_TOOL_NAMES set). It only still refuses outright if F&O
    # live trading was never configured at all.

    def place_live_option_order(self, underlying_symbol: str, strike: float, expiry_choice: str,
                                option_type: str, side: str, num_lots: int, order_type: str = "LIMIT",
                                limit_price: Optional[float] = None,
                                market_protection_pct: Optional[float] = None) -> dict:
        if self.live_fo_broker is None:
            raise TradingEngineError(
                "live F&O trading is not configured on this process (KITE_API_KEY/KITE_API_SECRET not set, "
                "or PHASE6_LIVE_TRADING_ENABLED != '1') — see this phase's README before setting these"
            )
        side = _validate_side(side)
        try:
            option_type_enum = OptionType(option_type.upper())
        except ValueError as e:
            raise TradingEngineError(f"option_type must be 'CE' or 'PE', got '{option_type}'") from e
        try:
            order_type_enum = OrderType(order_type.upper())
        except ValueError as e:
            raise TradingEngineError(f"order_type must be 'MARKET' or 'LIMIT', got '{order_type}'") from e
        underlying = self._get_underlying_or_raise(underlying_symbol)
        today = date_type.today()
        expiry = _resolve_expiry(self.expiry_resolver, underlying, expiry_choice, today)
        contract = OptionContract(underlying_symbol=underlying_symbol, strike=float(strike),
                                  expiry=expiry, option_type=option_type_enum)

        order = self.live_fo_broker.place_option_order(
            contract, side, num_lots, order_type_enum, limit_price=limit_price, tag="mcp_live_fo_order",
            market_protection_pct=market_protection_pct,
        )
        self.equity_engine.ledger.append("fo_LIVE_option_order_placed", {
            "underlying_symbol": underlying_symbol, "display_symbol": contract.display_symbol, "side": side,
            "num_lots": num_lots, "status": order.status.value, "broker_order_id": order.broker_order_id,
            "reject_reason": order.reject_reason,
        })
        return asdict(order) | {"display_symbol": contract.display_symbol, "side": order.side.value,
                                "order_type": order.order_type.value, "status": order.status.value}

    def place_live_future_order(self, underlying_symbol: str, side: str, num_lots: int, order_type: str = "LIMIT",
                                limit_price: Optional[float] = None,
                                market_protection_pct: Optional[float] = None) -> dict:
        if self.live_fo_broker is None:
            raise TradingEngineError(
                "live F&O trading is not configured on this process (KITE_API_KEY/KITE_API_SECRET not set, "
                "or PHASE6_LIVE_TRADING_ENABLED != '1') — see this phase's README before setting these"
            )
        side = _validate_side(side)
        try:
            order_type_enum = OrderType(order_type.upper())
        except ValueError as e:
            raise TradingEngineError(f"order_type must be 'MARKET' or 'LIMIT', got '{order_type}'") from e
        underlying = self._get_underlying_or_raise(underlying_symbol)
        today = date_type.today()
        expiry = _resolve_expiry(self.expiry_resolver, underlying, "MONTHLY", today)
        contract = FutureContract(underlying_symbol=underlying_symbol, expiry=expiry)

        order = self.live_fo_broker.place_future_order(
            contract, side, num_lots, order_type_enum, limit_price=limit_price, tag="mcp_live_fo_order",
            market_protection_pct=market_protection_pct,
        )
        self.equity_engine.ledger.append("fo_LIVE_future_order_placed", {
            "underlying_symbol": underlying_symbol, "display_symbol": contract.display_symbol, "side": side,
            "num_lots": num_lots, "status": order.status.value, "broker_order_id": order.broker_order_id,
            "reject_reason": order.reject_reason,
        })
        return asdict(order) | {"display_symbol": contract.display_symbol, "side": order.side.value,
                                "order_type": order.order_type.value, "status": order.status.value}


_fo_engine: Optional[FOTradingEngine] = None


def _build_live_fo_broker(equity_engine: TradingEngine) -> Optional[FOKiteConnectBroker]:
    # Deliberately reuses the EQUITY engine's own DailyReauthManager
    # (same Kite Connect login session backs both equity and F&O orders
    # for one broker account) rather than building a second one — see
    # this module's F&O section docstring.
    api_key = os.environ.get("KITE_API_KEY", "")
    if not api_key or equity_engine.daily_reauth_manager is None:
        return None
    return FOKiteConnectBroker(api_key, equity_engine.daily_reauth_manager)


def get_fo_engine() -> FOTradingEngine:
    global _fo_engine
    if _fo_engine is None:
        equity_engine = get_engine()
        fo_config = FOConfig()
        margin_config = MarginConfig()
        fo_data_provider = FOMarketDataProvider(seed=42)
        fo_risk_manager = FORiskManager(fo_config, margin_config)
        fo_paper_db_path = os.path.join(equity_engine.config.data_dir, "fo_paper_broker.db")
        fo_paper_broker = FOPaperBroker(fo_paper_db_path)
        fo_backtester = FOBacktester(
            fo_config, equity_config=equity_engine.config, margin_config=margin_config,
            signal_engine=equity_engine.paper_trader.signal_engine,
        )
        expiry_resolver = ExpiryResolver()
        live_fo_broker = _build_live_fo_broker(equity_engine)
        _fo_engine = FOTradingEngine(
            fo_config=fo_config, margin_config=margin_config, fo_data_provider=fo_data_provider,
            fo_risk_manager=fo_risk_manager, fo_paper_broker=fo_paper_broker, fo_backtester=fo_backtester,
            expiry_resolver=expiry_resolver, equity_engine=equity_engine, live_fo_broker=live_fo_broker,
        )
    return _fo_engine


def reset_fo_engine_for_tests() -> None:
    global _fo_engine
    _fo_engine = None
