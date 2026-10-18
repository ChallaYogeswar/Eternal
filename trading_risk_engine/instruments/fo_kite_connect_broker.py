"""
F&O Kite Connect broker.

Same real-not-simulated framing as broker/kite_connect_broker.py
(equity's): genuine calls against the real `kiteconnect` SDK, verified
against the installed package's actual method signatures, untestable
end-to-end in this sandbox for the same network reason.

The one thing that MUST differ from equity's broker, not by style choice
but because it would silently place wrong orders otherwise: **the
tradingsymbol is resolved from `kite.instruments()`, never pattern-
constructed.** `contracts.py`'s own `display_symbol` property says this
explicitly — it exists for logging/the ledger, not for submission to
Kite. Kite's real F&O tradingsymbol format has exchange-specific quirks
(and can change) that this project has no way to verify from a sandbox
with no route to fetch a real instrument dump; resolving by matching on
(name, strike, expiry, instrument_type) against the exchange's own
current list is the robust approach Kite's own documentation recommends,
and it fails loudly (a dedicated `InstrumentResolutionError`) if no
match is found, rather than guessing a symbol string and letting
Zerodha's API reject it — or worse, silently match something else.

Exchange: NFO for every NSE index (NIFTY, BANKNIFTY, FINNIFTY,
MIDCPNIFTY), BFO for SENSEX (BSE) — derived from `Underlying.exchange` in
underlyings.py, not hardcoded per call site.

Product: NRML (carries positions overnight) — this project's strategies
hold across multiple days (options weekly/monthly, futures monthly), not
intraday, so MIS (intraday-only, auto-squared-off) would be the wrong
default.
"""
from __future__ import annotations

import time
from typing import Callable, List, Optional

from ..broker.base import Broker
from ..broker.daily_reauth import DailyReauthManager
from ..models import Order, OrderSide, OrderStatus, OrderType
from .contracts import FutureContract, OptionContract
from .underlyings import Exchange


class BrokerAuthError(Exception):
    pass


class InstrumentResolutionError(Exception):
    """Raised when no matching contract is found in the exchange's own
    current instrument list — never silently falls back to a guessed
    tradingsymbol string."""


_EXCHANGE_TO_DERIVATIVES_SEGMENT = {Exchange.NSE: "NFO", Exchange.BSE: "BFO"}


class FOKiteConnectBroker(Broker):
    def __init__(
        self, api_key: str, reauth_manager: DailyReauthManager,
        kite_client_factory: Optional[Callable[[str], object]] = None,
        instrument_cache_ttl_seconds: float = 3600.0,
    ):
        self.api_key = api_key
        self.reauth_manager = reauth_manager
        self._kite_client_factory = kite_client_factory or self._default_kite_client_factory
        self._instrument_cache_ttl = instrument_cache_ttl_seconds
        self._instrument_cache: dict = {}  # segment -> (fetched_at, [instrument dicts])

    @staticmethod
    def _default_kite_client_factory(api_key: str):
        from kiteconnect import KiteConnect
        return KiteConnect(api_key=api_key)

    def _authenticated_client(self):
        token = self.reauth_manager.get_valid_access_token()
        if token is None:
            raise BrokerAuthError(
                "no valid Kite Connect access token for today — complete the daily login flow first"
            )
        client = self._kite_client_factory(self.api_key)
        client.set_access_token(token)
        return client

    def _get_instruments(self, client, segment: str) -> List[dict]:
        cached = self._instrument_cache.get(segment)
        if cached is not None and (time.time() - cached[0]) < self._instrument_cache_ttl:
            return cached[1]
        instruments = client.instruments(segment)
        self._instrument_cache[segment] = (time.time(), instruments)
        return instruments

    def _resolve_option(self, client, contract: OptionContract) -> dict:
        segment = _EXCHANGE_TO_DERIVATIVES_SEGMENT[contract.underlying.exchange]
        for row in self._get_instruments(client, segment):
            if (
                row.get("name") == contract.underlying_symbol
                and float(row.get("strike", -1)) == contract.strike
                and row.get("expiry") == contract.expiry
                and row.get("instrument_type") == contract.option_type.value
            ):
                return row
        raise InstrumentResolutionError(
            f"no {segment} instrument found matching {contract.display_symbol} "
            f"(name={contract.underlying_symbol}, strike={contract.strike}, expiry={contract.expiry}, "
            f"type={contract.option_type.value}) — the strike/expiry combination may not exist for "
            "this underlying, or the instrument dump may be stale"
        )

    def _resolve_future(self, client, contract: FutureContract) -> dict:
        segment = _EXCHANGE_TO_DERIVATIVES_SEGMENT[contract.underlying.exchange]
        for row in self._get_instruments(client, segment):
            if (
                row.get("name") == contract.underlying_symbol
                and row.get("expiry") == contract.expiry
                and row.get("instrument_type") == "FUT"
            ):
                return row
        raise InstrumentResolutionError(
            f"no {segment} future found matching {contract.display_symbol} "
            f"(name={contract.underlying_symbol}, expiry={contract.expiry})"
        )

    def place_option_order(
        self, contract: OptionContract, side: str, num_lots: int, order_type: OrderType,
        limit_price: Optional[float] = None, tag: str = "", market_protection_pct: Optional[float] = None,
    ) -> Order:
        """side is 'LONG' (buy to open) or 'SHORT' (sell to open) — this
        project's strategies only ever OPEN or fully CLOSE a position,
        never partially adjust one, so BUY/SELL here always means
        opening in that direction; closing is a separate call with the
        opposite transaction_type, issued by the caller (the gateway/MCP
        layer), not decided inside this class."""
        try:
            client = self._authenticated_client()
            instrument = self._resolve_option(client, contract)
        except (BrokerAuthError, InstrumentResolutionError) as e:
            reason = str(e)
        except Exception as e:  # e.g. network failure fetching the instrument dump — a live-order call must never raise
            reason = f"{type(e).__name__}: {e}"
        else:
            reason = None
        if reason is not None:
            return Order(order_id="", symbol=contract.display_symbol, side=OrderSide.BUY if side == "LONG" else OrderSide.SELL,
                        quantity=num_lots * contract.lot_size, order_type=order_type, limit_price=limit_price,
                        status=OrderStatus.REJECTED, reject_reason=reason, tag=tag)

        quantity = num_lots * contract.lot_size
        transaction_type = client.TRANSACTION_TYPE_BUY if side == "LONG" else client.TRANSACTION_TYPE_SELL
        kwargs = {"price": limit_price} if order_type == OrderType.LIMIT else {}
        if order_type == OrderType.MARKET:
            # Same rule as the equity broker (Kite Connect FAQ: market_protection=0 is treated as
            # "no protection" and the order is refused) — never send an unprotected MARKET order.
            if market_protection_pct is None or market_protection_pct <= 0:
                return Order(order_id="", symbol=contract.display_symbol,
                            side=OrderSide.BUY if side == "LONG" else OrderSide.SELL, quantity=quantity,
                            order_type=order_type, limit_price=limit_price, status=OrderStatus.REJECTED,
                            reject_reason="MARKET order requires a positive market_protection_pct "
                                          "(unprotected market orders are refused by Kite Connect)", tag=tag)
            kwargs["market_protection"] = market_protection_pct
        try:
            broker_order_id = client.place_order(
                variety=client.VARIETY_REGULAR, exchange=instrument["exchange"],
                tradingsymbol=instrument["tradingsymbol"], transaction_type=transaction_type,
                quantity=quantity, product=client.PRODUCT_NRML,
                order_type=(client.ORDER_TYPE_LIMIT if order_type == OrderType.LIMIT else client.ORDER_TYPE_MARKET),
                validity=client.VALIDITY_DAY, tag=tag, **kwargs,
            )
        except Exception as e:
            return Order(order_id="", symbol=contract.display_symbol,
                        side=OrderSide.BUY if side == "LONG" else OrderSide.SELL, quantity=quantity,
                        order_type=order_type, limit_price=limit_price, status=OrderStatus.REJECTED,
                        reject_reason=f"{type(e).__name__}: {e}", tag=tag)

        return Order(order_id=str(broker_order_id), symbol=instrument["tradingsymbol"],
                    side=OrderSide.BUY if side == "LONG" else OrderSide.SELL, quantity=quantity,
                    order_type=order_type, limit_price=limit_price, status=OrderStatus.PENDING,
                    broker_order_id=str(broker_order_id), tag=tag)

    def place_future_order(
        self, contract: FutureContract, side: str, num_lots: int, order_type: OrderType,
        limit_price: Optional[float] = None, tag: str = "", market_protection_pct: Optional[float] = None,
    ) -> Order:
        try:
            client = self._authenticated_client()
            instrument = self._resolve_future(client, contract)
        except (BrokerAuthError, InstrumentResolutionError) as e:
            reason = str(e)
        except Exception as e:  # e.g. network failure fetching the instrument dump — a live-order call must never raise
            reason = f"{type(e).__name__}: {e}"
        else:
            reason = None
        if reason is not None:
            return Order(order_id="", symbol=contract.display_symbol, side=OrderSide.BUY if side == "LONG" else OrderSide.SELL,
                        quantity=num_lots * contract.lot_size, order_type=order_type, limit_price=limit_price,
                        status=OrderStatus.REJECTED, reject_reason=reason, tag=tag)

        quantity = num_lots * contract.lot_size
        transaction_type = client.TRANSACTION_TYPE_BUY if side == "LONG" else client.TRANSACTION_TYPE_SELL
        kwargs = {"price": limit_price} if order_type == OrderType.LIMIT else {}
        if order_type == OrderType.MARKET:
            # Same rule as the equity broker (Kite Connect FAQ: market_protection=0 is treated as
            # "no protection" and the order is refused) — never send an unprotected MARKET order.
            if market_protection_pct is None or market_protection_pct <= 0:
                return Order(order_id="", symbol=contract.display_symbol,
                            side=OrderSide.BUY if side == "LONG" else OrderSide.SELL, quantity=quantity,
                            order_type=order_type, limit_price=limit_price, status=OrderStatus.REJECTED,
                            reject_reason="MARKET order requires a positive market_protection_pct "
                                          "(unprotected market orders are refused by Kite Connect)", tag=tag)
            kwargs["market_protection"] = market_protection_pct
        try:
            broker_order_id = client.place_order(
                variety=client.VARIETY_REGULAR, exchange=instrument["exchange"],
                tradingsymbol=instrument["tradingsymbol"], transaction_type=transaction_type,
                quantity=quantity, product=client.PRODUCT_NRML,
                order_type=(client.ORDER_TYPE_LIMIT if order_type == OrderType.LIMIT else client.ORDER_TYPE_MARKET),
                validity=client.VALIDITY_DAY, tag=tag, **kwargs,
            )
        except Exception as e:
            return Order(order_id="", symbol=contract.display_symbol,
                        side=OrderSide.BUY if side == "LONG" else OrderSide.SELL, quantity=quantity,
                        order_type=order_type, limit_price=limit_price, status=OrderStatus.REJECTED,
                        reject_reason=f"{type(e).__name__}: {e}", tag=tag)

        return Order(order_id=str(broker_order_id), symbol=instrument["tradingsymbol"],
                    side=OrderSide.BUY if side == "LONG" else OrderSide.SELL, quantity=quantity,
                    order_type=order_type, limit_price=limit_price, status=OrderStatus.PENDING,
                    broker_order_id=str(broker_order_id), tag=tag)

    # -- Broker ABC's shared interface --
    # F&O has two distinct entry points above (option vs future) rather
    # than one generic place_order, because the two need different
    # contract types and Kite instrument segments — place_order below
    # exists only to satisfy the shared ABC for code that generically
    # iterates brokers; real callers use the specific methods above.
    def place_order(self, symbol, side, quantity, order_type, limit_price=None, reference_price=None, tag=""):
        raise NotImplementedError(
            "FOKiteConnectBroker uses place_option_order/place_future_order — F&O contracts need "
            "strike/expiry/type information a generic symbol string can't carry"
        )

    def get_positions(self) -> List[dict]:
        # Returns raw dicts, not trading_risk_engine.models.Position (the shared
        # Broker ABC's type hint) — equity's Position has no field for
        # strike/expiry/option_type, which an F&O position can't be
        # described without. Duck-typed deliberately; see this class's
        # own module docstring on why F&O gets dedicated methods rather
        # than forcing the shared equity-shaped interface.
        client = self._authenticated_client()
        raw = client.positions()
        return raw.get("net", []) if isinstance(raw, dict) else []

    def get_margins(self) -> dict:
        client = self._authenticated_client()
        raw = client.margins()
        equity = raw.get("equity", {}) if isinstance(raw, dict) else {}
        return {"cash": float(equity.get("available", {}).get("live_balance", 0.0))}

    def cancel_order(self, order_id: str) -> bool:
        try:
            client = self._authenticated_client()
            client.cancel_order(variety=client.VARIETY_REGULAR, order_id=order_id)
            return True
        except Exception:
            return False

    def get_order(self, order_id: str) -> Optional[Order]:
        """Latest known state of an order, from Kite's order_history. Returns None when the order
        is unknown or the lookup fails — status polling must never raise into the caller."""
        try:
            client = self._authenticated_client()
            history = client.order_history(order_id)
        except Exception:
            return None
        if not history:
            return None
        latest = history[-1]
        status_map = {"COMPLETE": OrderStatus.FILLED, "REJECTED": OrderStatus.REJECTED,
                      "CANCELLED": OrderStatus.CANCELLED}
        status = status_map.get(latest.get("status", ""), OrderStatus.PENDING)
        return Order(
            order_id=order_id, symbol=str(latest.get("tradingsymbol", "")),
            side=(OrderSide.BUY if latest.get("transaction_type") == "BUY" else OrderSide.SELL),
            quantity=int(latest.get("quantity", 0)),
            order_type=(OrderType.LIMIT if latest.get("order_type") == "LIMIT" else OrderType.MARKET),
            limit_price=latest.get("price"), status=status, fill_price=latest.get("average_price"),
            broker_order_id=order_id, reject_reason=latest.get("status_message"),
        )
