"""
Kite Connect broker — real, not simulated, wiring against Zerodha's
official `kiteconnect` Python SDK. This class makes genuine API calls
when used; it cannot be exercised end-to-end in this sandbox (no network
route to Zerodha's endpoints, and this project holds no real brokerage
credentials — nor should it). Every method here is tested against a
FAKE client that mimics `kiteconnect.KiteConnect`'s real method
signatures exactly (inspected directly against the installed package at
build time, not guessed from memory), so the WRAPPING logic — how a
generic `Order`/`OrderSide`/`OrderType` maps onto Kite Connect's own
`variety`/`exchange`/`tradingsymbol`/`transaction_type`/`product`
vocabulary — is genuinely verified, even though the live HTTP round trip
underneath it is not.

CRITICAL — this class does NOT enforce any of the gates that make live
trading safe: not the daily-reauth check beyond "is there a token at
all," not the static-IP host-role check, not the order-rate limit, not
the top-trust-tier/non-voice confirmation requirement, and not
`config.live_trading_enabled`. Those all live in `regulatory.py` and
`trading_risk_engine/gateway.py`, ABOVE this class — the same layering QAP-P's vault
uses (the vault itself doesn't know about RCAP-P's rings; the rings
decide whether a call reaches the vault at all). This class must never be
constructed or called from anywhere except through `Phase6Gateway`'s
gated tool-call path.

Product type is always CNC (cash-and-carry / delivery) — this project
does not implement intraday margin trading, consistent with the
long-only, no-margin framing used throughout Phase 6.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from ..models import Order, OrderSide, OrderStatus, OrderType, Position
from .base import Broker
from .daily_reauth import DailyReauthManager


class BrokerAuthError(Exception):
    """Raised when there is no valid daily-reauth token — never silently
    falls back to a stale token or an unauthenticated call."""


def infer_exchange(symbol: str) -> str:
    if symbol.endswith(".NS"):
        return "NSE"
    if symbol.endswith(".BO"):
        return "BSE"
    raise ValueError(f"cannot infer an exchange from symbol '{symbol}' — expected a .NS or .BO suffix")


def strip_exchange_suffix(symbol: str) -> str:
    return symbol.rsplit(".", 1)[0]


class KiteConnectBroker(Broker):
    def __init__(
        self,
        api_key: str,
        reauth_manager: DailyReauthManager,
        kite_client_factory: Optional[Callable[[str], object]] = None,
    ):
        self.api_key = api_key
        self.reauth_manager = reauth_manager
        self._kite_client_factory = kite_client_factory or self._default_kite_client_factory

    @staticmethod
    def _default_kite_client_factory(api_key: str):
        from kiteconnect import KiteConnect
        return KiteConnect(api_key=api_key)

    def _authenticated_client(self):
        token = self.reauth_manager.get_valid_access_token()
        if token is None:
            raise BrokerAuthError(
                "no valid Kite Connect access token for today — complete the daily login flow "
                "(DailyReauthManager.get_login_url() -> log in -> exchange_request_token()) first"
            )
        client = self._kite_client_factory(self.api_key)
        client.set_access_token(token)
        return client

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType,
        limit_price: Optional[float] = None,
        reference_price: Optional[float] = None,  # unused for a real broker — kept for interface parity with PaperBroker
        tag: str = "",
        market_protection_pct: Optional[float] = None,
    ) -> Order:
        client = self._authenticated_client()
        exchange = infer_exchange(symbol)
        tradingsymbol = strip_exchange_suffix(symbol)

        kwargs = {}
        if order_type == OrderType.LIMIT:
            if limit_price is None:
                return Order(order_id="", symbol=symbol, side=side, quantity=quantity, order_type=order_type,
                            limit_price=limit_price, status=OrderStatus.REJECTED,
                            reject_reason="LIMIT order requires limit_price", tag=tag)
            kwargs["price"] = limit_price
        elif order_type == OrderType.MARKET:
            # Kite Connect FAQ: a market_protection value of 0 is treated
            # as an unprotected market order and rejected — regulatory.py
            # is expected to have already validated/supplied this before
            # this call is ever reached; this class does not invent a
            # default value for a live financial order parameter it isn't
            # confident about.
            if market_protection_pct is None or market_protection_pct <= 0:
                return Order(order_id="", symbol=symbol, side=side, quantity=quantity, order_type=order_type,
                            limit_price=limit_price, status=OrderStatus.REJECTED,
                            reject_reason="MARKET order requires a positive market_protection_pct "
                                          "(see regulatory.py's market-order gate)", tag=tag)
            kwargs["market_protection"] = market_protection_pct

        try:
            broker_order_id = client.place_order(
                variety=client.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                transaction_type=(client.TRANSACTION_TYPE_BUY if side == OrderSide.BUY else client.TRANSACTION_TYPE_SELL),
                quantity=quantity,
                product=client.PRODUCT_CNC,
                order_type=(client.ORDER_TYPE_LIMIT if order_type == OrderType.LIMIT else client.ORDER_TYPE_MARKET),
                validity=client.VALIDITY_DAY,
                tag=tag,
                **kwargs,
            )
        except Exception as e:
            return Order(order_id="", symbol=symbol, side=side, quantity=quantity, order_type=order_type,
                        limit_price=limit_price, status=OrderStatus.REJECTED,
                        reject_reason=f"{type(e).__name__}: {e}", tag=tag)

        # Kite order placement is asynchronous — acceptance by the
        # exchange gateway is not the same as being filled. PENDING here
        # means "accepted," not "executed"; get_order() below queries the
        # real, current status rather than this class guessing at it.
        return Order(order_id=str(broker_order_id), symbol=symbol, side=side, quantity=quantity,
                    order_type=order_type, limit_price=limit_price, status=OrderStatus.PENDING,
                    broker_order_id=str(broker_order_id), tag=tag)

    def get_positions(self) -> List[Position]:
        client = self._authenticated_client()
        raw = client.positions()
        net = raw.get("net", []) if isinstance(raw, dict) else []
        positions = []
        for p in net:
            quantity = int(p.get("quantity", 0))
            if quantity == 0:
                continue
            positions.append(Position(
                symbol=f"{p['tradingsymbol']}.NS" if p.get("exchange") == "NSE" else f"{p['tradingsymbol']}.BO",
                quantity=quantity, entry_price=float(p.get("average_price", 0.0)),
                entry_date="", stop_loss_price=0.0, take_profit_price=0.0, risk_amount=0.0,
            ))
        return positions

    def get_margins(self) -> dict:
        client = self._authenticated_client()
        raw = client.margins()
        equity = raw.get("equity", {}) if isinstance(raw, dict) else {}
        available = equity.get("available", {})
        return {"cash": float(available.get("live_balance", 0.0)), "raw": raw}

    def cancel_order(self, order_id: str) -> bool:
        client = self._authenticated_client()
        try:
            client.cancel_order(variety=client.VARIETY_REGULAR, order_id=order_id)
            return True
        except Exception:
            return False

    def get_order(self, order_id: str) -> Optional[Order]:
        client = self._authenticated_client()
        try:
            history = client.order_history(order_id)
        except Exception:
            return None
        if not history:
            return None
        latest = history[-1]
        status_map = {
            "COMPLETE": OrderStatus.FILLED, "REJECTED": OrderStatus.REJECTED,
            "CANCELLED": OrderStatus.CANCELLED,
        }
        status = status_map.get(latest.get("status", ""), OrderStatus.PENDING)
        symbol = f"{latest.get('tradingsymbol', '')}.{'NS' if latest.get('exchange') == 'NSE' else 'BO'}"
        return Order(
            order_id=order_id, symbol=symbol,
            side=(OrderSide.BUY if latest.get("transaction_type") == "BUY" else OrderSide.SELL),
            quantity=int(latest.get("quantity", 0)),
            order_type=(OrderType.LIMIT if latest.get("order_type") == "LIMIT" else OrderType.MARKET),
            limit_price=latest.get("price"), status=status,
            fill_price=latest.get("average_price"), broker_order_id=order_id,
            reject_reason=latest.get("status_message"),
        )
