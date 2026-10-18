"""
Paper trading loop.

Eternal_Master_Build_Plan.md Section 8, Phase 6 sub-phase A: "paper-
trading for several weeks minimum" — named as a real, standalone step,
not a synonym for backtesting. backtester.py answers "would this have
worked historically, replayed all at once." This module answers "run the
actual strategy forward, one real trading day at a time, with state that
survives the process restarting" — the thing you'd genuinely leave
running for weeks before ever touching sub-phase B.

Same no-lookahead discipline as the backtester, adapted for INCREMENTAL,
resumable operation instead of a single vectorized pass:

    call on day T:   fills whatever was DECIDED on day T-1's close,
                     using day T's OPEN (now genuinely known) as the
                     fill reference — then decides today's action from
                     day T's own close, to be filled on day T+1.

The "decided yesterday, filled today" state is persisted to SQLite
(`paper_trader_state.db`), not held in memory, because a real daily
process (a cron job, a systemd timer) is a NEW Python process each time
it runs — in-memory state would not survive that, and pretending
otherwise would make this module a demo, not the "real, standalone step"
the build plan calls for.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import date as date_type
from typing import Dict, List, Optional

from . import indicators as ind
from .broker.paper_broker import PaperBroker
from .config import Config
from .data_provider import MarketDataProvider, SimulatedMarketDataProvider
from .ledger_bridge import Ledger
from .models import OrderSide, OrderType, SignalDirection
from .risk import DrawdownCircuitBreaker, RiskManager, compute_stop_and_target
from .signals import SignalEngine


@dataclass
class DayResult:
    date: str
    fills: List[dict]
    new_pending_decisions: List[dict]
    circuit_breaker: dict
    equity: float
    cash: float
    positions_value: float


class _PendingDecisionStore:
    """One row per symbol with a decision awaiting tomorrow's fill.
    Deliberately its own tiny table/file, separate from PaperBroker's own
    cash/position/order tables — this is Phase 6's own bookkeeping about
    what it INTENDS to do, not the broker's record of what it HAS done."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_decisions (
                    symbol TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    decided_on TEXT NOT NULL,
                    atr_at_decision REAL,
                    close_at_decision REAL,
                    stop_loss_price REAL,
                    take_profit_price REAL,
                    risk_amount REAL,
                    quantity INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS last_processed (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_date TEXT NOT NULL
                )
            """)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_last_processed_date(self) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT last_date FROM last_processed WHERE id = 1").fetchone()
        return row["last_date"] if row else None

    def set_last_processed_date(self, value: str) -> None:
        with self._conn() as conn:
            conn.execute("INSERT INTO last_processed (id, last_date) VALUES (1, ?) "
                        "ON CONFLICT(id) DO UPDATE SET last_date = excluded.last_date", (value,))

    def set_pending(self, symbol: str, **fields) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM pending_decisions WHERE symbol = ?", (symbol,))
            columns = ", ".join(fields.keys())
            placeholders = ", ".join(["?"] * len(fields))
            conn.execute(
                f"INSERT INTO pending_decisions (symbol, {columns}) VALUES (?, {placeholders})",
                (symbol, *fields.values()),
            )

    def pop_pending(self, symbol: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM pending_decisions WHERE symbol = ?", (symbol,)).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM pending_decisions WHERE symbol = ?", (symbol,))
        return dict(row)

    def all_pending_symbols(self) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT symbol FROM pending_decisions").fetchall()
        return [r["symbol"] for r in rows]


class PaperTrader:
    def __init__(
        self,
        config: Config,
        data_provider: Optional[MarketDataProvider] = None,
        signal_engine: Optional[SignalEngine] = None,
        risk_manager: Optional[RiskManager] = None,
        broker: Optional[PaperBroker] = None,
        ledger: Optional[Ledger] = None,
        starting_cash: float = 100_000.0,
    ):
        self.config = config
        self.data_provider = data_provider or SimulatedMarketDataProvider()
        self.signal_engine = signal_engine or SignalEngine(config)
        self.risk_manager = risk_manager or RiskManager(config)
        self.broker = broker or PaperBroker(
            config.paper_broker_db_path, starting_cash=starting_cash,
            commission_pct=config.commission_pct, commission_flat=config.commission_flat,
            slippage_bps=config.slippage_bps,
        )
        self.ledger = ledger or Ledger(config.ledger_db_path)
        self.circuit_breaker = DrawdownCircuitBreaker(config)
        self._peak_equity = starting_cash
        state_db_path = os.path.join(config.data_dir, "paper_trader_state.db")
        self._store = _PendingDecisionStore(state_db_path)

    def _mark_to_market(self, latest_close: Dict[str, float]) -> float:
        positions = self.broker.get_positions()
        positions_value = sum(p.quantity * latest_close.get(p.symbol, p.entry_price) for p in positions)
        cash = self.broker.get_margins()["cash"]
        return cash + positions_value

    def run_trading_day(self, symbols: List[str], trading_date: date_type,
                        lookback_days: int = 400) -> DayResult:
        """Call this once per real (or simulated) trading day, in
        chronological order, one `trading_date` at a time — never call it
        twice for the same date, and never skip a date the market was
        open, or the "decided yesterday, filled today" pairing breaks.
        `lookback_days` only controls how much history is fetched for
        indicator warm-up; it does not affect correctness of the
        decide/fill pairing itself.
        """
        last_processed = self._store.get_last_processed_date()
        if last_processed is not None and trading_date.isoformat() <= last_processed:
            raise ValueError(
                f"trading_date {trading_date.isoformat()} is not after the last processed date "
                f"({last_processed}) — run_trading_day must be called once, in order, per real trading day"
            )

        start = trading_date.fromordinal(trading_date.toordinal() - lookback_days)
        bars = {symbol: self.data_provider.get_daily_bars(symbol, start, trading_date) for symbol in symbols}
        indicator_data = {
            symbol: ind.compute_all(
                df, atr_period=self.config.atr_period, rsi_period=self.config.rsi_period,
                bb_period=self.config.bb_period, bb_num_std=self.config.bb_num_std,
                sma_trend_period=self.config.sma_trend_period,
            )
            for symbol, df in bars.items()
        }

        fills: List[dict] = []
        latest_close = {symbol: float(df["close"].iloc[-1]) for symbol, df in indicator_data.items()}

        # ---- 1. Fill whatever was decided on the PRIOR call, using TODAY's open ----
        open_positions_by_symbol = {p.symbol: p for p in self.broker.get_positions()}
        for symbol in symbols:
            pending = self._store.pop_pending(symbol)
            if pending is None:
                continue
            today_open = float(indicator_data[symbol]["open"].iloc[-1])

            if pending["action"] == "BUY":
                order = self.broker.place_order(
                    symbol, OrderSide.BUY, int(pending["quantity"]), OrderType.MARKET,
                    reference_price=today_open, tag="paper_trader_entry",
                )
                if order.status.value == "FILLED":
                    # Re-anchor stop/target to the ACTUAL fill price, not
                    # the estimate they were sized against yesterday (the
                    # estimate was yesterday's CLOSE; the real fill is
                    # today's OPEN, which can differ from an overnight
                    # gap) — the same anchoring discipline
                    # backtester.py applies for exactly the same reason.
                    # The ATR used is still the one known AT THE DECISION
                    # (never a later, unknowable value): risk taken is
                    # fixed at decision time, only its price anchor moves
                    # to reflect where the fill actually happened.
                    atr_at_decision = pending["atr_at_decision"]
                    stop_loss_price, take_profit_price = compute_stop_and_target(
                        order.fill_price, atr_at_decision, self.config
                    )
                    risk_amount = pending["quantity"] * atr_at_decision * self.config.atr_stop_multiplier
                    self.broker.set_risk_levels(symbol, stop_loss_price, take_profit_price, risk_amount)
                else:
                    stop_loss_price = take_profit_price = None
                self.ledger.append("paper_trade_entry", {
                    "symbol": symbol, "order_id": order.order_id, "status": order.status.value,
                    "fill_price": order.fill_price, "quantity": order.quantity,
                    "stop_loss_price": stop_loss_price, "take_profit_price": take_profit_price,
                    "reject_reason": order.reject_reason,
                    "decided_on": pending["decided_on"], "filled_on": trading_date.isoformat(),
                })
                fills.append({"symbol": symbol, "action": "BUY", "order": order.__dict__})
            elif pending["action"] == "SELL":
                position = open_positions_by_symbol.get(symbol)
                quantity = position.quantity if position else int(pending.get("quantity") or 0)
                if quantity > 0:
                    order = self.broker.place_order(
                        symbol, OrderSide.SELL, quantity, OrderType.MARKET,
                        reference_price=today_open, tag="paper_trader_exit",
                    )
                    self.ledger.append("paper_trade_exit", {
                        "symbol": symbol, "order_id": order.order_id, "status": order.status.value,
                        "fill_price": order.fill_price, "quantity": order.quantity,
                        "reason": pending.get("action_reason", "signal_exit"),
                        "decided_on": pending["decided_on"], "filled_on": trading_date.isoformat(),
                    })
                    fills.append({"symbol": symbol, "action": "SELL", "order": order.__dict__})

        # ---- 2. Check stop-loss / take-profit for positions that were ALREADY
        # open before today's fills (a position entered in step 1, above,
        # is never checked against its own entry day's high/low — the same
        # "can't be stopped out on the bar you entered" convention
        # backtester.py uses, for the same reason: daily OHLC can't tell you
        # whether the low or the high came first relative to an entry that
        # also happened somewhere inside that same bar) ----
        for position in open_positions_by_symbol.values():
            symbol = position.symbol
            if symbol not in indicator_data:
                continue
            bar = indicator_data[symbol].iloc[-1]
            exit_reference = None
            reason = None
            if bar["low"] <= position.stop_loss_price:
                # Gap-aware, exactly like backtester.py: if today's OPEN
                # already sits through the stop, that open is the honest
                # fill reference, not the nominal (better) stop price.
                exit_reference = min(position.stop_loss_price, float(bar["open"]))
                reason = "stop_loss"
            elif bar["high"] >= position.take_profit_price:
                exit_reference = max(position.take_profit_price, float(bar["open"]))
                reason = "take_profit"
            if exit_reference is not None:
                order = self.broker.place_order(
                    symbol, OrderSide.SELL, position.quantity, OrderType.MARKET,
                    reference_price=exit_reference, tag=reason,
                )
                self.ledger.append("paper_trade_stop_or_target", {
                    "symbol": symbol, "order_id": order.order_id, "reason": reason,
                    "fill_price": order.fill_price, "quantity": order.quantity, "date": trading_date.isoformat(),
                })
                fills.append({"symbol": symbol, "action": "STOP_OR_TARGET", "order": order.__dict__})

        # ---- 3. Circuit breaker, computed from the broker's REAL current equity ----
        equity_now = self._mark_to_market(latest_close)
        self._peak_equity = max(self._peak_equity, equity_now)
        drawdown = (equity_now - self._peak_equity) / self._peak_equity if self._peak_equity > 0 else 0.0
        cb_status = self.circuit_breaker.check(trading_date, drawdown)
        if cb_status.newly_triggered:
            self.ledger.append("paper_trading_circuit_breaker_triggered", {
                "date": trading_date.isoformat(), "drawdown_pct": cb_status.drawdown_pct, "reason": cb_status.reason,
                "return_needed_to_recover_pct": cb_status.return_needed_to_recover_pct,
            })

        # ---- 4. Decide TODAY's action (to be filled tomorrow) ----
        new_pending: List[dict] = []
        open_symbols_after_fills = {p.symbol for p in self.broker.get_positions()}
        currently_pending = set(self._store.all_pending_symbols())

        for symbol in symbols:
            if symbol in currently_pending:
                continue  # already has an undecided action queued (shouldn't normally happen if called correctly)

            df = indicator_data[symbol]
            signal = self.signal_engine.generate(symbol, df)
            self.ledger.append("paper_trading_signal", {"symbol": symbol, **signal.as_ledger_payload()})

            if symbol in open_symbols_after_fills:
                if signal.direction == SignalDirection.SELL:
                    self._store.set_pending(symbol, action="SELL", decided_on=trading_date.isoformat(),
                                            atr_at_decision=None, close_at_decision=float(df["close"].iloc[-1]),
                                            stop_loss_price=None, take_profit_price=None, risk_amount=None,
                                            quantity=None)
                    new_pending.append({"symbol": symbol, "action": "SELL"})
                continue

            if not self.risk_manager.can_open_new_position(len(open_symbols_after_fills)):
                continue
            if cb_status.halted:
                continue
            if signal.direction != SignalDirection.BUY:
                continue

            atr_value = float(df["atr"].iloc[-1])
            close_value = float(df["close"].iloc[-1])
            positions_value_now = sum(
                p.quantity * latest_close.get(p.symbol, p.entry_price) for p in self.broker.get_positions()
            )
            size = self.risk_manager.size_position(
                symbol, entry_price=close_value, atr_value=atr_value,
                account_equity=equity_now, current_exposure_value=positions_value_now,
            )
            if size.shares <= 0:
                continue

            self._store.set_pending(
                symbol, action="BUY", decided_on=trading_date.isoformat(),
                atr_at_decision=atr_value, close_at_decision=close_value,
                stop_loss_price=size.stop_loss_price, take_profit_price=size.take_profit_price,
                risk_amount=size.risk_amount, quantity=size.shares,
            )
            new_pending.append({"symbol": symbol, "action": "BUY", "shares": size.shares})

        self._store.set_last_processed_date(trading_date.isoformat())

        final_positions_value = sum(
            p.quantity * latest_close.get(p.symbol, p.entry_price) for p in self.broker.get_positions()
        )
        final_cash = self.broker.get_margins()["cash"]

        return DayResult(
            date=trading_date.isoformat(), fills=fills, new_pending_decisions=new_pending,
            circuit_breaker={"halted": cb_status.halted, "newly_triggered": cb_status.newly_triggered,
                             "reason": cb_status.reason},
            equity=final_cash + final_positions_value, cash=final_cash, positions_value=final_positions_value,
        )
