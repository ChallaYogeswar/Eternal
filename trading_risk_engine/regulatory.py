"""
Regulatory gates for live order placement.

Eternal_Master_Build_Plan.md Section 7.2, "Regulatory landscape," names
five concrete, checkable constraints. Each one gets its own gate below,
each gate independently testable, and `LiveTradingGate` composes all of
them into the single check `trading_risk_engine/gateway.py` calls before a live order
is ever allowed to reach `KiteConnectBroker`:

1. Order placement must originate from a fixed IP (monitoring/position
   checks may come from anywhere) -> `StaticIPHostGate`.
2. IP changes are limited to one per calendar week -> `IPChangeTracker`
   (an operational tracking tool, not itself part of the per-order gate
   chain — see its own docstring for why).
3. 10 orders/second is the hard per-account rate ceiling -> `OrderRateLimiter`,
   deliberately configured well under that ceiling by default, not at it.
4. Daily re-authentication (2FA) — no long-lived refresh token ->
   `DailyReauthGate`, wrapping broker.daily_reauth.DailyReauthManager.
5. Kite Connect rejects unprotected market orders outright ->
   `validate_market_order`.

A NOTE ON WHAT THIS SANDBOX CAN AND CANNOT VERIFY: `StaticIPHostGate`
checks a configured ROLE flag (`Config.trading_host_role`), not an actual
public IP address. Determining a real public IP requires an outbound call
to an external IP-echo service (e.g. api.ipify.org), which is not reachable
from this sandbox — network egress here is restricted to package
registries and source-code hosts. On a real deployment, wire
`StaticIPHostGate` (or a small addition to it) to an actual IP lookup
against your broker-registered static IP; the role-flag mechanism here is
the honest, currently-testable stand-in, not a claim that IP verification
is fully implemented end-to-end.
"""
from __future__ import annotations

import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from .broker.daily_reauth import DailyReauthManager
from .config import Config
from .models import OrderType


@dataclass
class GateResult:
    allowed: bool
    reason: str


class StaticIPHostGate:
    """SEBI's April 2026 framework ties order placement to a single
    whitelisted static IP at the developer-account level — see this
    module's docstring for what this gate can and can't verify here."""

    def __init__(self, config: Config):
        self.config = config

    def check(self) -> GateResult:
        if not self.config.trading_host_role:
            return GateResult(False, "PHASE6_TRADING_HOST_ROLE is not set — live order placement must run "
                                     "from the machine designated as your fixed-IP order-placement host, never "
                                     "from a laptop or phone that could be anywhere")
        if self.config.trading_host_role != self.config.required_trading_host_role:
            return GateResult(False, f"this process reports host role '{self.config.trading_host_role}', "
                                     f"but live orders require '{self.config.required_trading_host_role}'")
        return GateResult(True, f"running with the required host role ({self.config.required_trading_host_role})")


class IPChangeTracker:
    """A monitoring/logging tool for the "one IP change per week"
    constraint — NOT wired into the per-order gate chain, deliberately.
    Kite Connect's IP whitelist is configured out-of-band, directly with
    the broker/developer console; this project cannot change your
    whitelisted IP for you, and silently blocking orders based on a local
    guess at "has the IP changed" could just as easily be WRONG (a home
    ISP's IP genuinely doesn't change for months at a time; a VPN or
    router reset might trigger a false change reading). This class exists
    so the constraint is at least tracked and visible — surfaced via the
    MCP layer's `trading_ip_change_history` tool — for a person to act on,
    not so software can silently refuse orders on a signal this
    imprecise.
    """

    def __init__(self, db_path: str, max_changes_per_week: int = 1):
        self.db_path = db_path
        self.max_changes_per_week = max_changes_per_week
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ip_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT NOT NULL,
                    recorded_at_utc REAL NOT NULL
                )
            """)

    def record_ip(self, ip_address: str, now: Optional[datetime] = None) -> GateResult:
        now = now or datetime.now(timezone.utc)
        with self._conn() as conn:
            last = conn.execute("SELECT ip_address FROM ip_history ORDER BY id DESC LIMIT 1").fetchone()
            if last is not None and last["ip_address"] == ip_address:
                return GateResult(True, "IP unchanged since last recorded")

            week_ago = (now - timedelta(days=7)).timestamp()
            changes_this_week = conn.execute(
                "SELECT COUNT(*) as n FROM ip_history WHERE recorded_at_utc >= ?", (week_ago,)
            ).fetchone()["n"]

            if last is not None and changes_this_week >= self.max_changes_per_week:
                return GateResult(
                    False,
                    f"IP has already changed {changes_this_week} time(s) in the last 7 days "
                    f"(limit {self.max_changes_per_week}/week) — Kite Connect's whitelist allows "
                    "changing the registered IP only this often; update the broker-side whitelist "
                    "manually if this change is intentional",
                )
            conn.execute("INSERT INTO ip_history (ip_address, recorded_at_utc) VALUES (?, ?)",
                        (ip_address, now.timestamp()))
            return GateResult(True, "IP change recorded, within the weekly allowance" if last else "first recorded IP")

    def history(self, limit: int = 20) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ip_address, recorded_at_utc FROM ip_history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{"ip_address": r["ip_address"],
                "recorded_at_utc": datetime.fromtimestamp(r["recorded_at_utc"], tz=timezone.utc).isoformat()}
               for r in rows]


class OrderRateLimiter:
    """Token-bucket-style limiter over a rolling 1-second window.
    Eternal_Master_Build_Plan.md Section 7.2: "10 orders/second is the
    hard rate-limit ceiling per client account... Personal, self-built,
    single-account use sits comfortably under this." Config's default
    (3/sec) is deliberately well under the 10/sec ceiling, not at it —
    headroom matters here because Kite Connect counts REJECTED orders
    toward the same limit, so a burst of locally-invalid orders (a bug,
    a misconfigured loop) can burn through rate-limit budget on requests
    that were never going to succeed anyway; sitting well under the
    ceiling leaves room for that without ever risking exchange-level
    throttling or a forced move into formal algo registration.
    """

    def __init__(self, max_per_second: float):
        self.max_per_second = max_per_second
        self._timestamps: deque = deque()

    def check_and_record(self, now: Optional[float] = None) -> GateResult:
        now = now if now is not None else time.time()
        while self._timestamps and now - self._timestamps[0] > 1.0:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_per_second:
            return GateResult(False, f"order rate limit reached ({self.max_per_second}/second) — wait and retry")
        self._timestamps.append(now)
        return GateResult(True, "within the configured rate limit")


class DailyReauthGate:
    def __init__(self, reauth_manager: DailyReauthManager):
        self.reauth_manager = reauth_manager

    def check(self) -> GateResult:
        status = self.reauth_manager.token_status()
        if not status.has_token:
            return GateResult(False, "no Kite Connect access token has ever been obtained — "
                                     "complete the daily login flow first")
        if not status.is_valid_now:
            return GateResult(False, f"today's Kite Connect access token expired at {status.valid_until_ist} IST — "
                                     "complete the daily login flow again")
        return GateResult(True, f"today's access token is valid for {status.hours_remaining} more hour(s)")


def validate_market_order(config: Config, order_type: OrderType, market_protection_pct: Optional[float]) -> GateResult:
    if order_type != OrderType.MARKET:
        return GateResult(True, "not a market order")
    if not config.allow_market_orders:
        return GateResult(False, "market orders are disabled by configuration (PHASE6_ALLOW_MARKET_ORDERS != '1') — "
                                 "use a LIMIT order instead")
    if market_protection_pct is None or market_protection_pct <= 0:
        return GateResult(False, "a market order requires a positive market_protection_pct — "
                                 "Kite Connect rejects unprotected (0%) market orders outright")
    return GateResult(True, f"market order permitted with {market_protection_pct}% protection")


class LiveTradingGate:
    """The single door everything above funnels through.
    `trading_risk_engine/gateway.py` calls `check_before_order` exactly once, and a
    live order proceeds only if every one of these returns allowed —
    the first failure stops the chain and is returned as-is, so the
    person always sees the SPECIFIC reason an order was refused, not a
    generic denial."""

    def __init__(
        self,
        config: Config,
        static_ip_gate: StaticIPHostGate,
        daily_reauth_gate: DailyReauthGate,
        rate_limiter: OrderRateLimiter,
    ):
        self.config = config
        self.static_ip_gate = static_ip_gate
        self.daily_reauth_gate = daily_reauth_gate
        self.rate_limiter = rate_limiter

    def check_before_order(self, order_type: OrderType, market_protection_pct: Optional[float] = None) -> GateResult:
        if not self.config.live_trading_enabled:
            return GateResult(False, "live trading is disabled by configuration (PHASE6_LIVE_TRADING_ENABLED != '1') "
                                     "— Eternal_Master_Build_Plan.md Section 8 requires sub-phase A to be fully "
                                     "proven, and Phase 5 to be running in daily use, before this is ever turned on")

        for gate_result in (
            self.static_ip_gate.check(),
            self.daily_reauth_gate.check(),
            validate_market_order(self.config, order_type, market_protection_pct),
        ):
            if not gate_result.allowed:
                return gate_result

        rate_result = self.rate_limiter.check_and_record()
        if not rate_result.allowed:
            return rate_result

        return GateResult(True, "all live-trading gates passed")
