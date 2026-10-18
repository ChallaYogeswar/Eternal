"""
Daily re-authentication for Kite Connect.

Eternal_Master_Build_Plan.md Section 7.2: "Daily re-authentication (with
2FA/TOTP) has been Kite Connect's standing requirement for individual API
users... build a real daily re-auth flow into the vault, not a
set-and-forget credential."

This module implements exactly Zerodha's own documented, supported flow:

    1. get_login_url()          -> a URL the PERSON opens and logs into
                                    manually (username, password, 2FA) —
                                    Zerodha's own login page, nothing else.
    2. exchange_request_token() -> after that manual login, Zerodha
                                    redirects to your app's registered
                                    redirect URL with a `request_token`
                                    query parameter; this exchanges that
                                    token for a real `access_token` via
                                    Kite Connect's own `/session/token`
                                    API (kiteconnect's `generate_session`).
    3. token_status()           -> reports whether today's token is
                                    still valid, without needing another
                                    network call.

Deliberately NOT implemented, as a considered choice rather than an
oversight: automating the login page itself (submitting the password and
TOTP programmatically, e.g. via a headless browser). That would be
circumventing the daily human-verification step the regulation exists to
require, not "building a re-auth flow into the vault" — the build plan's
own words describe automating the TOKEN EXCHANGE that happens AFTER
login, not the login itself. A person tapping through a login page once
a day is a feature of this design, matching the exact same "sensitive
actions get a human confirmation" principle Phase 5 already applies to
laptop/Home-Assistant control — not a gap to be automated away.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

# Indian Standard Time has no daylight saving and is a fixed UTC+5:30
# offset — using a fixed-offset timezone avoids any dependency on the
# host's tzdata database being installed/current, which a fresh
# Raspberry Pi image cannot be assumed to have out of the box.
IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class TokenStatus:
    has_token: bool
    obtained_at_ist: Optional[str]
    valid_until_ist: Optional[str]
    is_valid_now: bool
    hours_remaining: Optional[float]


class DailyReauthManager:
    def __init__(
        self,
        db_path: str,
        api_key: str,
        api_secret: str,
        cutover_hour_ist: int = 7,
        cutover_minute_ist: int = 30,
        kite_client_factory: Optional[Callable[[str], object]] = None,
    ):
        self.db_path = db_path
        self.api_key = api_key
        self.api_secret = api_secret
        self.cutover_hour_ist = cutover_hour_ist
        self.cutover_minute_ist = cutover_minute_ist
        # Dependency injection point for tests — defaults to constructing
        # a real `kiteconnect.KiteConnect`, but never imports it at
        # module load time so this class remains importable even in an
        # environment where the package isn't installed yet.
        self._kite_client_factory = kite_client_factory or self._default_kite_client_factory
        self._init_db()

    @staticmethod
    def _default_kite_client_factory(api_key: str):
        from kiteconnect import KiteConnect
        return KiteConnect(api_key=api_key)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    access_token TEXT NOT NULL,
                    obtained_at_utc REAL NOT NULL
                )
            """)

    def get_login_url(self) -> str:
        client = self._kite_client_factory(self.api_key)
        return client.login_url()

    def exchange_request_token(self, request_token: str) -> str:
        """The one real network call in this module — everything else is
        local bookkeeping. Raises whatever kiteconnect itself raises on
        failure (an invalid/expired request_token, a checksum mismatch,
        a network error); this function does not swallow or reinterpret
        that exception, since a broken daily-auth flow is exactly the
        kind of failure that should surface loudly, not be logged and
        moved past."""
        client = self._kite_client_factory(self.api_key)
        data = client.generate_session(request_token, api_secret=self.api_secret)
        access_token = data["access_token"]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tokens (access_token, obtained_at_utc) VALUES (?, ?)",
                (access_token, time.time()),
            )
        return access_token

    def _latest_token_row(self):
        with self._conn() as conn:
            return conn.execute(
                "SELECT access_token, obtained_at_utc FROM tokens ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def _next_cutover_after(self, moment_ist: datetime) -> datetime:
        candidate = moment_ist.replace(
            hour=self.cutover_hour_ist, minute=self.cutover_minute_ist, second=0, microsecond=0
        )
        if candidate <= moment_ist:
            candidate += timedelta(days=1)
        return candidate

    def token_status(self, now_utc: Optional[datetime] = None) -> TokenStatus:
        row = self._latest_token_row()
        if row is None:
            return TokenStatus(has_token=False, obtained_at_ist=None, valid_until_ist=None,
                              is_valid_now=False, hours_remaining=None)

        obtained_at_ist = datetime.fromtimestamp(row["obtained_at_utc"], tz=timezone.utc).astimezone(IST)
        valid_until_ist = self._next_cutover_after(obtained_at_ist)
        now_ist = (now_utc.astimezone(IST) if now_utc is not None else datetime.now(timezone.utc).astimezone(IST))
        is_valid = now_ist < valid_until_ist
        remaining = (valid_until_ist - now_ist).total_seconds() / 3600.0 if is_valid else 0.0

        return TokenStatus(
            has_token=True,
            obtained_at_ist=obtained_at_ist.isoformat(),
            valid_until_ist=valid_until_ist.isoformat(),
            is_valid_now=is_valid,
            hours_remaining=round(remaining, 2),
        )

    def get_valid_access_token(self, now_utc: Optional[datetime] = None) -> Optional[str]:
        status = self.token_status(now_utc)
        if not status.is_valid_now:
            return None
        return self._latest_token_row()["access_token"]
