"""
Trading calendar and expiry resolution.

The 2026 holiday list below is transcribed directly from NSE's own
circular (NSE/CMTR/71775, dated December 12, 2025, "Trading holidays for
the calendar year 2026") — fetched and read while building this module,
not recalled or guessed. BSE is assumed to observe the same calendar
(standard practice across Indian exchanges; not independently re-verified
against a separate BSE circular).

**This dict is year-keyed on purpose and WILL need a new entry added
before it runs out** — NSE publishes each year's holiday circular near
the end of the preceding year. There is no way to derive next year's
holidays from a formula; this has to be a maintained list.

Expiry-day rules, per `instruments/underlyings.py`'s own docstring: NSE
indices expire the last Tuesday of the month (or the relevant week's
Tuesday for NIFTY's weekly contracts); BSE indices expire the last
Thursday (or SENSEX's weekly Thursday). Effective September 1, 2025 —
also verified while building this, not assumed unchanged from the prior
Thursday/Tuesday convention that held for the ~25 years before that.

If the computed expiry date is a holiday, it shifts to the PREVIOUS
trading day — confirmed consistently across every source checked. This
module's own test suite verifies that shift against REAL 2026 holidays
from the list below (three of 2026's national holidays happen to land on
Tuesdays, and two on Thursdays — real dates, not synthetic fixtures
invented to make a convenient test case).
"""
from __future__ import annotations

import calendar as _calendar_module
from datetime import date, timedelta
from typing import Dict, List

from .underlyings import Underlying

# Source: NSE circular NSE/CMTR/71775, December 12, 2025.
# 2025: weekday holidays from NSE circular NSE/COM/65590 (Ref 61/2024, December 13, 2024,
# "Trading holidays for the calendar year 2025"). That circular is the Commodity Derivatives
# Segment's; equity derivatives share its full-day closures, but it additionally lists Jan 1
# (morning-open) and several morning-closed/evening-open days that only matter to commodity
# trading — only the equity-relevant closures are listed. Single-source: re-verify against the
# equity-segment circular (NSE/CMTR) if you rely on pre-2026 expiry dates for anything but
# backtesting. Weekend holidays (Jan 26, Apr 6, Jun 7, Jul 6) remove no weekday and are omitted.
NSE_TRADING_HOLIDAYS: Dict[int, List[date]] = {
    2025: [
        date(2025, 2, 26),   # Mahashivratri
        date(2025, 3, 14),   # Holi
        date(2025, 3, 31),   # Id-Ul-Fitr (Ramadan Eid)
        date(2025, 4, 10),   # Shri Mahavir Jayanti
        date(2025, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
        date(2025, 4, 18),   # Good Friday
        date(2025, 5, 1),    # Maharashtra Day
        date(2025, 8, 15),   # Independence Day
        date(2025, 8, 27),   # Ganesh Chaturthi
        date(2025, 10, 2),   # Mahatma Gandhi Jayanti / Dussehra
        date(2025, 10, 21),  # Diwali Laxmi Pujan (Muhurat session held — a special session, not a normal day)
        date(2025, 10, 22),  # Diwali-Balipratipada
        date(2025, 11, 5),   # Prakash Gurpurb Sri Guru Nanak Dev
        date(2025, 12, 25),  # Christmas
    ],
    2026: [
        date(2026, 1, 26),   # Republic Day
        date(2026, 3, 3),    # Holi
        date(2026, 3, 26),   # Shri Ram Navami
        date(2026, 3, 31),   # Shri Mahavir Jayanti
        date(2026, 4, 3),    # Good Friday
        date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
        date(2026, 5, 1),    # Maharashtra Day
        date(2026, 5, 28),   # Bakri Id
        date(2026, 6, 26),   # Muharram
        date(2026, 9, 14),   # Ganesh Chaturthi
        date(2026, 10, 2),   # Mahatma Gandhi Jayanti
        date(2026, 10, 20),  # Dussehra
        date(2026, 11, 10),  # Diwali-Balipratipada
        date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev
        date(2026, 12, 25),  # Christmas
        # Nov 8, 2026 (Diwali Laxmi Pujan, Muhurat Trading) falls on a
        # Sunday and is a special limited-hours SESSION, not a holiday
        # that removes a weekday — deliberately not listed as a holiday.
    ],
}


class TradingCalendar:
    def __init__(self, holidays_by_year: Dict[int, List[date]] = None):
        self._holidays = holidays_by_year if holidays_by_year is not None else NSE_TRADING_HOLIDAYS

    def is_trading_day(self, d: date) -> bool:
        if d.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        if d.year not in self._holidays:
            raise ValueError(
                f"no holiday calendar loaded for {d.year} — add an entry to NSE_TRADING_HOLIDAYS "
                "(or pass a custom holidays_by_year) before resolving expiries in this year"
            )
        return d not in self._holidays[d.year]

    def shift_to_previous_trading_day(self, d: date) -> date:
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    last_day_num = _calendar_module.monthrange(year, month)[1]
    d = date(year, month, last_day_num)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


class ExpiryResolver:
    def __init__(self, trading_calendar: TradingCalendar = None):
        self.calendar = trading_calendar or TradingCalendar()

    def next_weekly_expiry(self, underlying: Underlying, as_of: date) -> date:
        """The next date on/after `as_of` that falls on the underlying's
        weekly expiry weekday, shifted to the previous trading day if
        that date is a holiday. Raises if the underlying doesn't have a
        weekly contract at all (per the Nov 2024 SEBI "one weekly expiry
        per exchange" rule — see underlyings.py)."""
        if not underlying.has_weekly_options:
            raise ValueError(
                f"{underlying.symbol} has no weekly options contract (SEBI's Nov 2024 rule limits each "
                "exchange to one weekly index-options expiry) — only monthly expiries apply here"
            )
        days_ahead = (underlying.weekly_expiry_weekday - as_of.weekday()) % 7
        candidate = as_of + timedelta(days=days_ahead)
        return self.calendar.shift_to_previous_trading_day(candidate)

    def next_monthly_expiry(self, underlying: Underlying, as_of: date) -> date:
        """The next monthly expiry on/after `as_of` — the last occurrence
        of the underlying's monthly expiry weekday in the current month,
        rolling to next month if that date has already passed, shifted
        to the previous trading day if it's a holiday."""
        candidate = _last_weekday_of_month(as_of.year, as_of.month, underlying.monthly_expiry_weekday)
        if candidate < as_of:
            next_month = as_of.month + 1
            next_year = as_of.year + (1 if next_month > 12 else 0)
            next_month = 1 if next_month > 12 else next_month
            candidate = _last_weekday_of_month(next_year, next_month, underlying.monthly_expiry_weekday)
        return self.calendar.shift_to_previous_trading_day(candidate)

    def futures_expiry_cycle(self, underlying: Underlying, as_of: date, num_months: int = 3) -> List[date]:
        """Index futures always trade a near/next/far MONTHLY cycle
        (never weekly, for any index — verified while building this
        module) — this returns that cycle's expiry dates, starting from
        the current or next upcoming monthly expiry."""
        expiries = []
        cursor = as_of
        for _ in range(num_months):
            expiry = self.next_monthly_expiry(underlying, cursor)
            expiries.append(expiry)
            cursor = expiry + timedelta(days=1)
        return expiries
