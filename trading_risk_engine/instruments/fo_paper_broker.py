"""
F&O paper broker.

Same real-accounting-fake-market principle as broker/paper_broker.py
(equity's), and deliberately a SEPARATE class/schema, not a retrofit —
the cash mechanics are genuinely different: buying equity or a long
option both cost cash upfront, but a SHORT option CREDITS cash (premium
received) while blocking margin instead, and a FUTURES position moves no
premium at all, only margin. Reusing equity's `PaperBroker` would mean
bolting a third cash-flow model onto a class whose schema and logic
already correctly assume "positions cost money to open," which options-
selling and futures both violate.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional

from .contracts import FOPosition, FutureContract, OptionContract, OptionType


@dataclass
class FOOrder:
    order_id: str
    display_symbol: str
    side: str  # LONG or SHORT (opening) — closes are recorded as the position's own exit
    num_lots: int
    price: float
    status: str  # FILLED or REJECTED
    reject_reason: Optional[str] = None


class FOPaperBroker:
    def __init__(self, db_path: str, starting_cash: float = 1_000_000.0):
        self.db_path = db_path
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fo_cash (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cash REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fo_positions (
                    position_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,               -- 'OPTION' or 'FUTURE'
                    underlying_symbol TEXT NOT NULL,
                    strike REAL,                      -- NULL for futures
                    expiry TEXT NOT NULL,
                    option_type TEXT,                 -- 'CE'/'PE', NULL for futures
                    side TEXT NOT NULL,                -- LONG or SHORT
                    num_lots INTEGER NOT NULL,
                    lot_size INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_date TEXT NOT NULL,
                    margin_blocked REAL NOT NULL,
                    stop_loss_price REAL NOT NULL,
                    take_profit_price REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fo_orders (
                    order_id TEXT PRIMARY KEY,
                    display_symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    num_lots INTEGER NOT NULL,
                    price REAL NOT NULL,
                    status TEXT NOT NULL,
                    reject_reason TEXT,
                    created_at REAL NOT NULL
                )
            """)
            existing = conn.execute("SELECT cash FROM fo_cash WHERE id = 1").fetchone()
            if existing is None:
                conn.execute("INSERT INTO fo_cash (id, cash) VALUES (1, ?)", (starting_cash,))

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_margins(self) -> dict:
        with self._conn() as conn:
            cash = conn.execute("SELECT cash FROM fo_cash WHERE id = 1").fetchone()["cash"]
            margin_blocked = conn.execute("SELECT COALESCE(SUM(margin_blocked), 0) AS m FROM fo_positions").fetchone()["m"]
        return {"cash": cash, "margin_blocked": margin_blocked, "available": cash - margin_blocked}

    def _record_order(self, conn, display_symbol: str, side: str, num_lots: int, price: float, status: str,
                      reject_reason: Optional[str] = None) -> FOOrder:
        order_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO fo_orders (order_id, display_symbol, side, num_lots, price, status, reject_reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, display_symbol, side, num_lots, price, status, reject_reason, time.time()),
        )
        return FOOrder(order_id=order_id, display_symbol=display_symbol, side=side, num_lots=num_lots,
                       price=price, status=status, reject_reason=reject_reason)

    def open_option_position(
        self, contract: OptionContract, side: str, num_lots: int, premium: float, margin_required: float,
        stop_loss_price: float, take_profit_price: float, entry_date: str,
    ) -> FOOrder:
        """side='LONG' costs cash (premium paid); side='SHORT' credits
        cash (premium received) and blocks margin instead — the two are
        NOT mirror images of the same cash flow, on purpose (see module
        docstring)."""
        with self._conn() as conn:
            margins = conn.execute("SELECT cash FROM fo_cash WHERE id = 1").fetchone()
            cash = margins["cash"]
            quantity = num_lots * contract.lot_size
            if side == "LONG":
                cost = premium * quantity
                if cost > cash:
                    return self._record_order(conn, contract.display_symbol, side, num_lots, premium, "REJECTED",
                                              f"insufficient cash: need {cost:.2f}, have {cash:.2f}")
                new_cash = cash - cost
            else:
                if margin_required > cash:
                    return self._record_order(conn, contract.display_symbol, side, num_lots, premium, "REJECTED",
                                              f"insufficient margin: need {margin_required:.2f}, have {cash:.2f}")
                new_cash = cash + premium * quantity

            position_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO fo_positions (position_id, kind, underlying_symbol, strike, expiry, option_type, "
                "side, num_lots, lot_size, entry_price, entry_date, margin_blocked, stop_loss_price, take_profit_price) "
                "VALUES (?, 'OPTION', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (position_id, contract.underlying_symbol, contract.strike, contract.expiry.isoformat(),
                 contract.option_type.value, side, num_lots, contract.lot_size, premium, entry_date,
                 margin_required, stop_loss_price, take_profit_price),
            )
            conn.execute("UPDATE fo_cash SET cash = ? WHERE id = 1", (new_cash,))
            return self._record_order(conn, contract.display_symbol, side, num_lots, premium, "FILLED")

    def open_future_position(
        self, contract: FutureContract, side: str, num_lots: int, price: float, margin_required: float,
        stop_loss_price: float, take_profit_price: float, entry_date: str,
    ) -> FOOrder:
        """No premium changes hands for a future — only margin, for
        either direction (long or short)."""
        with self._conn() as conn:
            cash = conn.execute("SELECT cash FROM fo_cash WHERE id = 1").fetchone()["cash"]
            if margin_required > cash:
                return self._record_order(conn, contract.display_symbol, side, num_lots, price, "REJECTED",
                                          f"insufficient margin: need {margin_required:.2f}, have {cash:.2f}")
            position_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO fo_positions (position_id, kind, underlying_symbol, strike, expiry, option_type, "
                "side, num_lots, lot_size, entry_price, entry_date, margin_blocked, stop_loss_price, take_profit_price) "
                "VALUES (?, 'FUTURE', ?, NULL, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                (position_id, contract.underlying_symbol, contract.expiry.isoformat(), side, num_lots,
                 contract.lot_size, price, entry_date, margin_required, stop_loss_price, take_profit_price),
            )
            return self._record_order(conn, contract.display_symbol, side, num_lots, price, "FILLED")

    def close_position(self, position_id: str, exit_price: float) -> Optional[float]:
        """Returns the realized P&L, or None if no such position exists.

        Audit E-10: `exit_price` used to be annotated `str` while the body does arithmetic on it. It must be a positive,
        finite number; anything else is a ValueError before any state is touched."""
        import math
        if isinstance(exit_price, bool) or not isinstance(exit_price, (int, float)) \
                or not math.isfinite(exit_price) or exit_price <= 0:
            raise ValueError(f"exit_price must be a positive finite number, got {exit_price!r}")
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM fo_positions WHERE position_id = ?", (position_id,)).fetchone()
            if row is None:
                return None
            quantity = row["num_lots"] * row["lot_size"]
            if row["side"] == "LONG":
                pnl = (exit_price - row["entry_price"]) * quantity
                cash_delta = exit_price * quantity  # sell what was bought
            else:
                pnl = (row["entry_price"] - exit_price) * quantity
                cash_delta = -exit_price * quantity  # buy back what was sold; margin also released below

            cash = conn.execute("SELECT cash FROM fo_cash WHERE id = 1").fetchone()["cash"]
            new_cash = cash + cash_delta if row["kind"] == "OPTION" else cash + pnl
            conn.execute("UPDATE fo_cash SET cash = ? WHERE id = 1", (new_cash,))
            conn.execute("DELETE FROM fo_positions WHERE position_id = ?", (position_id,))
            return pnl

    def get_positions(self) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM fo_positions").fetchall()
        return [dict(r) for r in rows]
