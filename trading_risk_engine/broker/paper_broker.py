"""
Paper broker — a REAL, persistent order/position/cash ledger with
SIMULATED fills. "Simulated" describes the price source only: there is no
live exchange on the other end of this, so `place_order` requires the
caller to supply `reference_price` (typically today's close from a
MarketDataProvider, or a live quote). Everything else — the SQLite
persistence, the cash accounting, the position bookkeeping, the
commission/slippage math, the long-only enforcement — is exactly what a
real broker integration would also need to get right, which is the point
of building this as a genuine implementation rather than an in-memory
stub: Eternal_Master_Build_Plan.md Section 8's own sub-phase A calls for
"paper-trading for several weeks minimum" as a real, standalone step
before any live order is ever placed, not a throwaway placeholder.

Long-only, no margin: a SELL can never exceed the currently held
quantity for that symbol (no naive shorting) — matching the cash-account
retail-equity framing used throughout this phase.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from typing import List, Optional

from ..models import Order, OrderSide, OrderStatus, OrderType, Position
from .base import Broker


class PaperBroker(Broker):
    def __init__(
        self,
        db_path: str,
        starting_cash: float = 100_000.0,
        commission_pct: float = 0.0003,
        commission_flat: float = 20.0,
        slippage_bps: float = 5.0,
    ):
        self.db_path = db_path
        self.commission_pct = commission_pct
        self.commission_flat = commission_flat
        self.slippage_bps = slippage_bps
        self._init_db(starting_cash)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self, starting_cash: float) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cash (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    balance REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_date TEXT NOT NULL,
                    stop_loss_price REAL NOT NULL,
                    take_profit_price REAL NOT NULL,
                    risk_amount REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    order_type TEXT NOT NULL,
                    limit_price REAL,
                    status TEXT NOT NULL,
                    requested_at REAL NOT NULL,
                    filled_at REAL,
                    fill_price REAL,
                    commission REAL NOT NULL DEFAULT 0.0,
                    slippage REAL NOT NULL DEFAULT 0.0,
                    reject_reason TEXT,
                    tag TEXT NOT NULL DEFAULT ''
                )
            """)
            existing = conn.execute("SELECT balance FROM cash WHERE id = 1").fetchone()
            if existing is None:
                conn.execute("INSERT INTO cash (id, balance) VALUES (1, ?)", (starting_cash,))

    def _commission(self, price: float, quantity: int) -> float:
        return self.commission_flat + self.commission_pct * price * quantity

    def _fill_price(self, reference_price: float, side: OrderSide) -> float:
        factor = self.slippage_bps / 10_000.0
        if side == OrderSide.BUY:
            return reference_price * (1.0 + factor)
        return reference_price * (1.0 - factor)

    def _get_cash(self, conn: sqlite3.Connection) -> float:
        return conn.execute("SELECT balance FROM cash WHERE id = 1").fetchone()["balance"]

    def _set_cash(self, conn: sqlite3.Connection, value: float) -> None:
        conn.execute("UPDATE cash SET balance = ? WHERE id = 1", (value,))

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType,
        limit_price: Optional[float] = None,
        reference_price: Optional[float] = None,
        tag: str = "",
    ) -> Order:
        order_id = str(uuid.uuid4())
        requested_at = time.time()

        if quantity <= 0:
            return self._reject(order_id, symbol, side, quantity, order_type, limit_price, requested_at, tag,
                                "quantity must be positive")

        if order_type == OrderType.MARKET:
            if reference_price is None or reference_price <= 0:
                return self._reject(order_id, symbol, side, quantity, order_type, limit_price, requested_at, tag,
                                    "a MARKET paper order requires a positive reference_price to simulate a fill against")
            fill_price = self._fill_price(reference_price, side)
            return self._try_fill(order_id, symbol, side, quantity, order_type, limit_price, requested_at, tag, fill_price)

        if order_type == OrderType.LIMIT:
            if limit_price is None or limit_price <= 0:
                return self._reject(order_id, symbol, side, quantity, order_type, limit_price, requested_at, tag,
                                    "a LIMIT order requires a positive limit_price")
            if reference_price is None:
                return self._store_pending(order_id, symbol, side, quantity, order_type, limit_price, requested_at, tag)
            crosses = (side == OrderSide.BUY and reference_price <= limit_price) or \
                      (side == OrderSide.SELL and reference_price >= limit_price)
            if not crosses:
                return self._store_pending(order_id, symbol, side, quantity, order_type, limit_price, requested_at, tag)
            # A real limit order fills at your limit price or better, never worse — no adverse
            # slippage applied here, unlike a market order, which is the entire point of using one.
            fill_price = limit_price if side == OrderSide.BUY else limit_price
            fill_price = min(limit_price, reference_price) if side == OrderSide.BUY else max(limit_price, reference_price)
            return self._try_fill(order_id, symbol, side, quantity, order_type, limit_price, requested_at, tag, fill_price)

        return self._reject(order_id, symbol, side, quantity, order_type, limit_price, requested_at, tag,
                            f"unsupported order_type: {order_type}")

    def _reject(self, order_id, symbol, side, quantity, order_type, limit_price, requested_at, tag, reason) -> Order:
        order = Order(order_id=order_id, symbol=symbol, side=side, quantity=quantity, order_type=order_type,
                      limit_price=limit_price, status=OrderStatus.REJECTED, requested_at=requested_at,
                      reject_reason=reason, tag=tag)
        self._persist(order)
        return order

    def _store_pending(self, order_id, symbol, side, quantity, order_type, limit_price, requested_at, tag) -> Order:
        order = Order(order_id=order_id, symbol=symbol, side=side, quantity=quantity, order_type=order_type,
                      limit_price=limit_price, status=OrderStatus.PENDING, requested_at=requested_at, tag=tag)
        self._persist(order)
        return order

    def _try_fill(self, order_id, symbol, side, quantity, order_type, limit_price, requested_at, tag, fill_price) -> Order:
        commission = self._commission(fill_price, quantity)
        with self._conn() as conn:
            cash = self._get_cash(conn)
            existing = conn.execute("SELECT * FROM positions WHERE symbol = ?", (symbol,)).fetchone()

            if side == OrderSide.BUY:
                cost = fill_price * quantity + commission
                if cost > cash:
                    return self._reject(order_id, symbol, side, quantity, order_type, limit_price, requested_at, tag,
                                        f"insufficient paper cash: need {cost:.2f}, have {cash:.2f}")
                self._set_cash(conn, cash - cost)
                if existing is None:
                    conn.execute(
                        "INSERT INTO positions (symbol, quantity, entry_price, entry_date, stop_loss_price, "
                        "take_profit_price, risk_amount) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (symbol, quantity, fill_price, time.strftime("%Y-%m-%d"), 0.0, 0.0, 0.0),
                    )
                else:
                    new_qty = existing["quantity"] + quantity
                    # Volume-weighted average entry price across adds — the
                    # stop/target on file stay whatever they were (this
                    # broker doesn't recompute risk levels on its own;
                    # paper_trader.py owns that decision, matching the
                    # backtester's "never moved after entry" convention).
                    new_avg = (existing["entry_price"] * existing["quantity"] + fill_price * quantity) / new_qty
                    conn.execute("UPDATE positions SET quantity = ?, entry_price = ? WHERE symbol = ?",
                                (new_qty, new_avg, symbol))
            else:  # SELL
                held = existing["quantity"] if existing else 0
                if quantity > held:
                    return self._reject(order_id, symbol, side, quantity, order_type, limit_price, requested_at, tag,
                                        f"cannot sell {quantity} shares of {symbol}, only {held} held (long-only, no shorting)")
                proceeds = fill_price * quantity - commission
                self._set_cash(conn, cash + proceeds)
                remaining = held - quantity
                if remaining == 0:
                    conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
                else:
                    conn.execute("UPDATE positions SET quantity = ? WHERE symbol = ?", (remaining, symbol))

            order = Order(order_id=order_id, symbol=symbol, side=side, quantity=quantity, order_type=order_type,
                         limit_price=limit_price, status=OrderStatus.FILLED, requested_at=requested_at,
                         filled_at=time.time(), fill_price=fill_price, commission=commission, tag=tag)
            self._persist(order, conn=conn)
            return order

    def _persist(self, order: Order, conn: Optional[sqlite3.Connection] = None) -> None:
        owns_conn = conn is None
        conn = conn or self._conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO orders (order_id, symbol, side, quantity, order_type, limit_price, "
                "status, requested_at, filled_at, fill_price, commission, slippage, reject_reason, tag) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (order.order_id, order.symbol, order.side.value, order.quantity, order.order_type.value,
                 order.limit_price, order.status.value, order.requested_at, order.filled_at, order.fill_price,
                 order.commission, order.slippage, order.reject_reason, order.tag),
            )
            if owns_conn:
                conn.commit()
        finally:
            if owns_conn:
                conn.close()

    def get_positions(self) -> List[Position]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM positions").fetchall()
        return [
            Position(symbol=r["symbol"], quantity=r["quantity"], entry_price=r["entry_price"],
                    entry_date=r["entry_date"], stop_loss_price=r["stop_loss_price"],
                    take_profit_price=r["take_profit_price"], risk_amount=r["risk_amount"])
            for r in rows
        ]

    def set_risk_levels(self, symbol: str, stop_loss_price: float, take_profit_price: float,
                        risk_amount: float) -> bool:
        """Additive, not part of the shared `Broker` ABC (a real broker
        doesn't need to be told your stop-loss for ITS bookkeeping — your
        own system tracks that). `_try_fill` deliberately inserts a new
        position with placeholder 0.0 risk levels (see its comment) and
        leaves setting the REAL levels to the caller — paper_trader.py
        calls this immediately after a BUY fills, using the ACTUAL fill
        price (not the pre-fill estimate the position was sized against)
        combined with the ATR known at decision time, the same anchoring
        backtester.py uses. Returns False (not an exception) if no open
        position exists for `symbol` — calling this for a symbol you
        don't hold is a caller bug worth surfacing as a clear False, not
        worth crashing over."""
        with self._conn() as conn:
            existing = conn.execute("SELECT symbol FROM positions WHERE symbol = ?", (symbol,)).fetchone()
            if existing is None:
                return False
            conn.execute(
                "UPDATE positions SET stop_loss_price = ?, take_profit_price = ?, risk_amount = ? WHERE symbol = ?",
                (stop_loss_price, take_profit_price, risk_amount, symbol),
            )
        return True

    def get_margins(self) -> dict:
        with self._conn() as conn:
            cash = self._get_cash(conn)
        return {"cash": cash, "note": "equity requires marking positions to a live price via compute_equity()"}

    def compute_equity(self, price_lookup: dict) -> float:
        """`price_lookup` maps symbol -> current price. Positions for a
        symbol not present in the lookup are valued at their own entry
        price (a conservative fallback, not silently excluded)."""
        cash = self.get_margins()["cash"]
        positions_value = 0.0
        for pos in self.get_positions():
            price = price_lookup.get(pos.symbol, pos.entry_price)
            positions_value += pos.quantity * price
        return cash + positions_value

    def cancel_order(self, order_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT status FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if row is None or row["status"] != OrderStatus.PENDING.value:
                return False
            conn.execute("UPDATE orders SET status = ? WHERE order_id = ?", (OrderStatus.CANCELLED.value, order_id))
            return True

    def get_order(self, order_id: str) -> Optional[Order]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if row is None:
            return None
        return Order(
            order_id=row["order_id"], symbol=row["symbol"], side=OrderSide(row["side"]),
            quantity=row["quantity"], order_type=OrderType(row["order_type"]), limit_price=row["limit_price"],
            status=OrderStatus(row["status"]), requested_at=row["requested_at"], filled_at=row["filled_at"],
            fill_price=row["fill_price"], commission=row["commission"], slippage=row["slippage"],
            reject_reason=row["reject_reason"], tag=row["tag"],
        )
