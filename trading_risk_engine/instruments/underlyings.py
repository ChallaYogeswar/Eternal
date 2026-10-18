"""
Underlying index registry.

Every figure here was checked against current sources while building
this module (August 2026), not recalled from training data — both lot
sizes and weekly-expiry eligibility have changed materially in the last
two years and would have been confidently wrong from memory:

- SEBI mandated (effective Nov 2024) that each exchange may offer only
  ONE weekly index-options expiry. NSE kept it for NIFTY 50; BSE kept it
  for SENSEX. BANKNIFTY, FINNIFTY, and MIDCPNIFTY lost their weekly
  options entirely and are now MONTHLY-ONLY — a fact that is very easy
  to get wrong if you remember "BankNifty has weekly options," which was
  true for years and no longer is.
- Lot sizes were revised again for the January 2026 contract series
  (NSE circular dated November 28, 2025, keeping contract notional value
  within SEBI's mandated band as index levels move) — see the exact
  figures below.

**These numbers WILL be revised again** — NSE has changed lot sizes
roughly twice a year historically. This module is a plain lookup table
specifically so updating it later is a data change, not a code change;
re-verify against NSE's current circular before any real deployment,
not just once at build time.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"


@dataclass(frozen=True)
class Underlying:
    symbol: str
    exchange: Exchange
    lot_size: int
    has_weekly_options: bool
    # NSE index derivatives (options AND futures) expire the last Tuesday
    # of the month; BSE index derivatives expire the last Thursday —
    # effective September 1, 2025 (previously Thursday/Tuesday
    # respectively, for ~25 years before that in NSE's case). Futures
    # have never had weekly contracts for any index — always a
    # near/next/far MONTHLY cycle — so this single field covers both
    # monthly options and all futures expiries for this underlying.
    monthly_expiry_weekday: int  # Python's Monday=0 ... Sunday=6
    weekly_expiry_weekday: int  # meaningless unless has_weekly_options is True


_MONDAY, _TUESDAY, _WEDNESDAY, _THURSDAY, _FRIDAY = 0, 1, 2, 3, 4

UNDERLYINGS: dict = {
    "NIFTY": Underlying(
        symbol="NIFTY", exchange=Exchange.NSE, lot_size=65,
        has_weekly_options=True, monthly_expiry_weekday=_TUESDAY, weekly_expiry_weekday=_TUESDAY,
    ),
    "BANKNIFTY": Underlying(
        symbol="BANKNIFTY", exchange=Exchange.NSE, lot_size=30,
        has_weekly_options=False, monthly_expiry_weekday=_TUESDAY, weekly_expiry_weekday=_TUESDAY,
    ),
    "FINNIFTY": Underlying(
        symbol="FINNIFTY", exchange=Exchange.NSE, lot_size=60,
        has_weekly_options=False, monthly_expiry_weekday=_TUESDAY, weekly_expiry_weekday=_TUESDAY,
    ),
    "MIDCPNIFTY": Underlying(
        symbol="MIDCPNIFTY", exchange=Exchange.NSE, lot_size=120,
        has_weekly_options=False, monthly_expiry_weekday=_TUESDAY, weekly_expiry_weekday=_TUESDAY,
    ),
    "SENSEX": Underlying(
        symbol="SENSEX", exchange=Exchange.BSE, lot_size=20,
        has_weekly_options=True, monthly_expiry_weekday=_THURSDAY, weekly_expiry_weekday=_THURSDAY,
    ),
}


def get_underlying(symbol: str) -> Underlying:
    key = symbol.upper()
    if key not in UNDERLYINGS:
        raise ValueError(
            f"unknown underlying '{symbol}' — this build supports index options/futures only "
            f"(stage 1 of the F&O extension), currently: {sorted(UNDERLYINGS.keys())}"
        )
    return UNDERLYINGS[key]
