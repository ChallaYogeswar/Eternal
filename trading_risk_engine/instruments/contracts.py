"""
Contract models — the F&O equivalent of trading_risk_engine/models.py's Position/Trade,
but lot-based (not share-based) and carrying strike/expiry/option-type
where relevant.

Display symbols here (e.g. "NIFTY26AUG24500CE") are for LOGGING AND THE
LEDGER ONLY. Kite Connect's own documentation is explicit that a caller
should resolve the actual tradingsymbol/instrument_token from
`kite.instruments("NFO")` (or "BFO" for BSE) rather than construct it by
pattern — exact formatting has exchange-specific quirks this module does
not attempt to replicate. `instruments/fo_kite_connect_broker.py` does
the real resolution; this module's `display_symbol` is never sent to
the broker.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .underlyings import Underlying, get_underlying


class OptionType(str, Enum):
    CALL = "CE"
    PUT = "PE"


@dataclass(frozen=True)
class OptionContract:
    underlying_symbol: str
    strike: float
    expiry: date
    option_type: OptionType

    @property
    def underlying(self) -> Underlying:
        return get_underlying(self.underlying_symbol)

    @property
    def lot_size(self) -> int:
        return self.underlying.lot_size

    @property
    def display_symbol(self) -> str:
        strike_str = str(int(self.strike)) if self.strike == int(self.strike) else str(self.strike)
        return f"{self.underlying_symbol}{self.expiry.strftime('%y%b').upper()}{strike_str}{self.option_type.value}"

    def is_expired(self, as_of: date) -> bool:
        return as_of > self.expiry


@dataclass(frozen=True)
class FutureContract:
    underlying_symbol: str
    expiry: date

    @property
    def underlying(self) -> Underlying:
        return get_underlying(self.underlying_symbol)

    @property
    def lot_size(self) -> int:
        return self.underlying.lot_size

    @property
    def display_symbol(self) -> str:
        return f"{self.underlying_symbol}{self.expiry.strftime('%y%b').upper()}FUT"

    def is_expired(self, as_of: date) -> bool:
        return as_of > self.expiry


@dataclass
class FOPosition:
    """One open F&O position — always an integer number of LOTS, never a
    share/unit count. `contract` is either an OptionContract or a
    FutureContract; `side` records whether this is a long (bought) or
    short (sold/written) position, which matters enormously for options
    specifically (a short option carries margin obligations and,
    for calls, theoretically unlimited risk — very different from a
    long option, whose maximum loss is the premium already paid)."""
    contract: object  # OptionContract | FutureContract
    side: str  # "LONG" or "SHORT"
    num_lots: int
    entry_price: float  # premium (options) or futures price, per unit — NOT per lot
    entry_date: str
    margin_blocked: float
    stop_loss_price: float
    take_profit_price: float

    @property
    def quantity(self) -> int:
        return self.num_lots * self.contract.lot_size

    @property
    def is_option(self) -> bool:
        return isinstance(self.contract, OptionContract)

    @property
    def signed_quantity(self) -> int:
        """Positive for LONG, negative for SHORT — the sign P&L math
        needs (a SHORT position gains when price falls, the opposite of
        LONG), so this is computed once here rather than re-derived with
        an if/else at every call site."""
        return self.quantity if self.side == "LONG" else -self.quantity
