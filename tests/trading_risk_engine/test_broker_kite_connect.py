import os
from datetime import datetime, timezone

import pytest

from trading_risk_engine.broker.daily_reauth import DailyReauthManager
from trading_risk_engine.broker.kite_connect import (
    BrokerAuthError,
    KiteConnectBroker,
    infer_exchange,
    strip_exchange_suffix,
)
from trading_risk_engine.models import OrderSide, OrderStatus, OrderType

DB = "/tmp/phase6_kite_broker_test.db"


@pytest.fixture(autouse=True)
def _clean_db():
    if os.path.exists(DB):
        os.remove(DB)
    yield
    if os.path.exists(DB):
        os.remove(DB)


class FakeKiteClient:
    """Mimics the real kiteconnect.KiteConnect surface this wrapper uses —
    constants and method signatures confirmed directly against the
    installed package (see this module's own docstring notes in
    kite_connect_broker.py)."""
    VARIETY_REGULAR = "regular"
    PRODUCT_CNC = "CNC"
    ORDER_TYPE_LIMIT = "LIMIT"
    ORDER_TYPE_MARKET = "MARKET"
    TRANSACTION_TYPE_BUY = "BUY"
    TRANSACTION_TYPE_SELL = "SELL"
    VALIDITY_DAY = "DAY"

    def __init__(self, api_key):
        self.api_key = api_key
        self.access_token = None
        self.place_order_calls = []
        self.next_order_id = "OID12345"
        self.raise_on_place_order = None
        self._positions = {"net": []}
        self._margins = {"equity": {"available": {"live_balance": 0.0}}}
        self._order_history = {}

    def set_access_token(self, access_token):
        self.access_token = access_token

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        if self.raise_on_place_order:
            raise self.raise_on_place_order
        return self.next_order_id

    def positions(self):
        return self._positions

    def margins(self, segment=None):
        return self._margins

    def cancel_order(self, variety, order_id, parent_order_id=None):
        return order_id

    def order_history(self, order_id):
        return self._order_history.get(order_id, [])


def _broker_with_valid_token(monkeypatch):
    reauth = DailyReauthManager(
        db_path=DB, api_key="testkey", api_secret="testsecret",
        kite_client_factory=lambda k: _TokenOnlyFakeClient(),
    )
    # Obtained "just now" (real wall-clock time) rather than at a fixed
    # historical moment — the exact cutover-boundary math already has
    # its own dedicated, precise tests in test_daily_reauth.py; these
    # broker-wrapper tests only need SOME valid token to exist so the
    # auth gate passes, so they use the manager's default "now".
    reauth.exchange_request_token("rt")
    client = FakeKiteClient("testkey")
    broker = KiteConnectBroker(api_key="testkey", reauth_manager=reauth, kite_client_factory=lambda k: client)
    return broker, client


class _TokenOnlyFakeClient:
    def generate_session(self, request_token, api_secret):
        return {"access_token": "fake-live-token"}


# ---------------------------------------------------------------------------
# Symbol <-> Kite vocabulary helpers
# ---------------------------------------------------------------------------

def test_infer_exchange_nse_and_bse():
    assert infer_exchange("RELIANCE.NS") == "NSE"
    assert infer_exchange("SOMESTOCK.BO") == "BSE"


def test_infer_exchange_rejects_unknown_suffix():
    with pytest.raises(ValueError):
        infer_exchange("RELIANCE")


def test_strip_exchange_suffix():
    assert strip_exchange_suffix("RELIANCE.NS") == "RELIANCE"


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------

def test_place_order_raises_broker_auth_error_without_a_valid_token():
    reauth = DailyReauthManager(db_path=DB, api_key="k", api_secret="s",
                                kite_client_factory=lambda k: _TokenOnlyFakeClient())
    broker = KiteConnectBroker(api_key="k", reauth_manager=reauth, kite_client_factory=lambda k: FakeKiteClient("k"))
    with pytest.raises(BrokerAuthError):
        broker.place_order("RELIANCE.NS", OrderSide.BUY, 10, OrderType.LIMIT, limit_price=2500.0)


# ---------------------------------------------------------------------------
# Order placement — correct mapping onto Kite's own vocabulary
# ---------------------------------------------------------------------------

def test_limit_buy_maps_onto_kite_vocabulary_correctly(monkeypatch):
    broker, client = _broker_with_valid_token(monkeypatch)
    order = broker.place_order("RELIANCE.NS", OrderSide.BUY, 10, OrderType.LIMIT, limit_price=2500.0, tag="test")

    assert order.status == OrderStatus.PENDING
    assert order.broker_order_id == "OID12345"
    call = client.place_order_calls[0]
    assert call["exchange"] == "NSE"
    assert call["tradingsymbol"] == "RELIANCE"
    assert call["transaction_type"] == "BUY"
    assert call["product"] == "CNC"
    assert call["order_type"] == "LIMIT"
    assert call["price"] == 2500.0
    assert call["variety"] == "regular"
    assert call["validity"] == "DAY"
    assert call["tag"] == "test"


def test_market_sell_requires_market_protection(monkeypatch):
    broker, client = _broker_with_valid_token(monkeypatch)
    rejected = broker.place_order("TCS.NS", OrderSide.SELL, 5, OrderType.MARKET)
    assert rejected.status == OrderStatus.REJECTED
    assert "market_protection" in rejected.reject_reason
    assert client.place_order_calls == []  # never even reached the real API call


def test_market_order_with_protection_reaches_kite(monkeypatch):
    broker, client = _broker_with_valid_token(monkeypatch)
    order = broker.place_order("TCS.NS", OrderSide.SELL, 5, OrderType.MARKET, market_protection_pct=3.0)
    assert order.status == OrderStatus.PENDING
    assert client.place_order_calls[0]["market_protection"] == 3.0
    assert client.place_order_calls[0]["order_type"] == "MARKET"
    assert client.place_order_calls[0]["transaction_type"] == "SELL"


def test_limit_order_without_price_is_rejected_before_reaching_kite(monkeypatch):
    broker, client = _broker_with_valid_token(monkeypatch)
    order = broker.place_order("TCS.NS", OrderSide.BUY, 5, OrderType.LIMIT, limit_price=None)
    assert order.status == OrderStatus.REJECTED
    assert client.place_order_calls == []


def test_kite_api_exception_is_caught_and_reported_not_propagated(monkeypatch):
    broker, client = _broker_with_valid_token(monkeypatch)
    client.raise_on_place_order = RuntimeError("insufficient funds")
    order = broker.place_order("TCS.NS", OrderSide.BUY, 5, OrderType.LIMIT, limit_price=100.0)
    assert order.status == OrderStatus.REJECTED
    assert "insufficient funds" in order.reject_reason


# ---------------------------------------------------------------------------
# Positions / margins / order status parsing
# ---------------------------------------------------------------------------

def test_get_positions_parses_kite_net_positions(monkeypatch):
    broker, client = _broker_with_valid_token(monkeypatch)
    client._positions = {"net": [
        {"tradingsymbol": "RELIANCE", "exchange": "NSE", "quantity": 10, "average_price": 2500.0},
        {"tradingsymbol": "CLOSEDOUT", "exchange": "NSE", "quantity": 0, "average_price": 100.0},
    ]}
    positions = broker.get_positions()
    assert len(positions) == 1  # the zero-quantity row is excluded
    assert positions[0].symbol == "RELIANCE.NS"
    assert positions[0].quantity == 10
    assert positions[0].entry_price == pytest.approx(2500.0)


def test_get_margins_parses_kite_response(monkeypatch):
    broker, client = _broker_with_valid_token(monkeypatch)
    client._margins = {"equity": {"available": {"live_balance": 54321.0}}}
    margins = broker.get_margins()
    assert margins["cash"] == pytest.approx(54321.0)


def test_get_order_maps_kite_status_to_internal_status(monkeypatch):
    broker, client = _broker_with_valid_token(monkeypatch)
    client._order_history["OID1"] = [
        {"status": "OPEN"},
        {"status": "COMPLETE", "tradingsymbol": "TCS", "exchange": "NSE", "transaction_type": "BUY",
         "quantity": 5, "order_type": "LIMIT", "price": 100.0, "average_price": 99.5},
    ]
    order = broker.get_order("OID1")
    assert order.status == OrderStatus.FILLED
    assert order.symbol == "TCS.NS"
    assert order.fill_price == pytest.approx(99.5)


def test_get_order_returns_none_for_unknown_order(monkeypatch):
    broker, client = _broker_with_valid_token(monkeypatch)
    assert broker.get_order("NEVER-PLACED") is None


def test_cancel_order_returns_true_on_success_false_on_exception(monkeypatch):
    broker, client = _broker_with_valid_token(monkeypatch)
    assert broker.cancel_order("OID1") is True

    def _raise(*a, **k):
        raise RuntimeError("cannot cancel a completed order")
    client.cancel_order = _raise
    assert broker.cancel_order("OID1") is False
