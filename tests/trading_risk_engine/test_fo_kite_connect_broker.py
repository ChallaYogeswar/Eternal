import datetime as dt
import os

import pytest

from trading_risk_engine.broker.daily_reauth import DailyReauthManager
from trading_risk_engine.instruments.contracts import FutureContract, OptionContract, OptionType
from trading_risk_engine.instruments.fo_kite_connect_broker import (
    BrokerAuthError,
    FOKiteConnectBroker,
    InstrumentResolutionError,
)
from trading_risk_engine.models import OrderStatus, OrderType

DB = "/tmp/trading_risk_engine_fo_kite_broker_test.db"


@pytest.fixture(autouse=True)
def _clean_db():
    if os.path.exists(DB):
        os.remove(DB)
    yield
    if os.path.exists(DB):
        os.remove(DB)


class FakeFOKiteClient:
    """Mirrors the real kiteconnect.KiteConnect surface FOKiteConnectBroker
    uses, including instruments() — constants and the parsed-row shape
    confirmed directly against the installed package (see
    fo_kite_connect_broker.py's own docstring). Note strike=0.0 (not
    None) for the futures row — the real SDK's _parse_instruments always
    does float(row["strike"]) unconditionally on every row, so a future/
    equity row's strike is always a float (0.0), never None; an earlier
    version of this fixture used None here and broke the resolver's own
    float(row.get("strike", -1)) call — a bug in the test fixture, not
    the broker, caught by running it."""
    VARIETY_REGULAR = "regular"
    PRODUCT_NRML = "NRML"
    ORDER_TYPE_LIMIT = "LIMIT"
    ORDER_TYPE_MARKET = "MARKET"
    TRANSACTION_TYPE_BUY = "BUY"
    TRANSACTION_TYPE_SELL = "SELL"
    VALIDITY_DAY = "DAY"

    def __init__(self, api_key):
        self.api_key = api_key
        self.access_token = None
        self.place_order_calls = []
        self.next_order_id = "FO-OID-999"
        self.raise_on_place_order = None
        self._instruments = {
            "NFO": [
                {"name": "NIFTY", "strike": 24500.0, "expiry": dt.date(2026, 8, 18), "instrument_type": "CE",
                 "exchange": "NFO", "tradingsymbol": "NIFTY26818CE", "lot_size": 65},
                {"name": "NIFTY", "strike": 24500.0, "expiry": dt.date(2026, 8, 18), "instrument_type": "PE",
                 "exchange": "NFO", "tradingsymbol": "NIFTY26818PE", "lot_size": 65},
                {"name": "NIFTY", "strike": 0.0, "expiry": dt.date(2026, 8, 25), "instrument_type": "FUT",
                 "exchange": "NFO", "tradingsymbol": "NIFTY26AUGFUT", "lot_size": 65},
            ],
            "BFO": [
                {"name": "SENSEX", "strike": 80000.0, "expiry": dt.date(2026, 8, 20), "instrument_type": "PE",
                 "exchange": "BFO", "tradingsymbol": "SENSEX2682080000PE", "lot_size": 20},
            ],
        }

    def set_access_token(self, access_token):
        self.access_token = access_token

    def instruments(self, segment):
        return self._instruments.get(segment, [])

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        if self.raise_on_place_order:
            raise self.raise_on_place_order
        return self.next_order_id


def _manager(token="valid-token", fake_client=None):
    manager = DailyReauthManager(DB, "testkey", "testsecret")
    if token is not None:
        manager.get_valid_access_token = lambda now_utc=None: token
    client = fake_client or FakeFOKiteClient("testkey")
    broker = FOKiteConnectBroker("testkey", manager, kite_client_factory=lambda api_key: client)
    return broker, client


def _call_contract():
    return OptionContract(underlying_symbol="NIFTY", strike=24500.0, expiry=dt.date(2026, 8, 18),
                          option_type=OptionType.CALL)


def _future_contract():
    return FutureContract(underlying_symbol="NIFTY", expiry=dt.date(2026, 8, 25))


def test_option_order_resolves_the_real_tradingsymbol_from_instruments(tmp_path):
    broker, client = _manager()
    order = broker.place_option_order(_call_contract(), "LONG", num_lots=1, order_type=OrderType.LIMIT,
                                      limit_price=150.0, tag="test")
    assert order.status == OrderStatus.PENDING
    assert order.symbol == "NIFTY26818CE"
    call_kwargs = client.place_order_calls[0]
    assert call_kwargs["tradingsymbol"] == "NIFTY26818CE"
    assert call_kwargs["exchange"] == "NFO"
    assert call_kwargs["product"] == "NRML"
    assert call_kwargs["quantity"] == 65


def test_option_order_rejected_clearly_when_no_matching_instrument_exists():
    broker, client = _manager()
    bad_contract = OptionContract(underlying_symbol="NIFTY", strike=99999.0, expiry=dt.date(2026, 8, 18),
                                  option_type=OptionType.CALL)
    order = broker.place_option_order(bad_contract, "LONG", num_lots=1, order_type=OrderType.LIMIT, limit_price=1.0)
    assert order.status == OrderStatus.REJECTED
    assert "no NFO instrument found" in order.reject_reason
    assert len(client.place_order_calls) == 0


def test_future_order_resolves_from_instruments_and_uses_nrml():
    broker, client = _manager()
    order = broker.place_future_order(_future_contract(), "LONG", num_lots=2, order_type=OrderType.MARKET,
                                      market_protection_pct=3.0)
    assert order.status == OrderStatus.PENDING
    assert order.symbol == "NIFTY26AUGFUT"
    call_kwargs = client.place_order_calls[0]
    assert call_kwargs["exchange"] == "NFO"
    assert call_kwargs["product"] == "NRML"
    assert call_kwargs["quantity"] == 130


def test_sensex_option_resolves_to_bfo_not_nfo():
    broker, client = _manager()
    sensex_put = OptionContract(underlying_symbol="SENSEX", strike=80000.0, expiry=dt.date(2026, 8, 20),
                                option_type=OptionType.PUT)
    order = broker.place_option_order(sensex_put, "SHORT", num_lots=1, order_type=OrderType.LIMIT, limit_price=200.0)
    assert order.status == OrderStatus.PENDING
    call_kwargs = client.place_order_calls[0]
    assert call_kwargs["exchange"] == "BFO"
    assert call_kwargs["transaction_type"] == "SELL"


def test_option_order_rejected_without_a_valid_daily_token():
    broker, client = _manager(token=None)
    order = broker.place_option_order(_call_contract(), "LONG", num_lots=1, order_type=OrderType.LIMIT,
                                      limit_price=150.0)
    assert order.status == OrderStatus.REJECTED
    assert "no valid Kite Connect access token" in order.reject_reason
    assert len(client.place_order_calls) == 0


def test_option_order_propagates_a_real_placement_failure_as_a_rejection():
    broker, client = _manager()
    client.raise_on_place_order = ConnectionError("Host not in allowlist: api.kite.trade")
    order = broker.place_option_order(_call_contract(), "LONG", num_lots=1, order_type=OrderType.LIMIT,
                                      limit_price=150.0)
    assert order.status == OrderStatus.REJECTED
    assert "Host not in allowlist" in order.reject_reason


def test_instrument_list_is_cached_not_refetched_every_call():
    broker, client = _manager()
    original_instruments = client.instruments
    call_count = {"n": 0}

    def counting_instruments(segment):
        call_count["n"] += 1
        return original_instruments(segment)
    client.instruments = counting_instruments

    broker.place_option_order(_call_contract(), "LONG", num_lots=1, order_type=OrderType.LIMIT, limit_price=1.0)
    broker.place_option_order(_call_contract(), "LONG", num_lots=1, order_type=OrderType.LIMIT, limit_price=1.0)
    assert call_count["n"] == 1


def test_generic_place_order_is_not_supported_directly():
    broker, _ = _manager()
    with pytest.raises(NotImplementedError):
        broker.place_order("NIFTY.NS", None, 1, OrderType.LIMIT)


# -- Regression: found by actually running demo_exit_criteria.py in the merged project --
# A network failure while fetching the instrument dump used to escape place_*_order as a raw
# exception (only BrokerAuthError/InstrumentResolutionError were caught). A live-order call must
# degrade to a REJECTED Order, never raise — same contract as equity's KiteConnectBroker.
class _NetworkDownClient(FakeFOKiteClient):
    def instruments(self, segment):
        raise ConnectionError("proxy tunnel refused")


def test_option_order_rejected_not_raised_when_instrument_fetch_hits_network_error():
    broker, client = _manager(fake_client=_NetworkDownClient("testkey"))
    order = broker.place_option_order(_call_contract(), "LONG", num_lots=1, order_type=OrderType.LIMIT,
                                      limit_price=150.0)
    assert order.status == OrderStatus.REJECTED
    assert "ConnectionError" in order.reject_reason
    assert len(client.place_order_calls) == 0


def test_future_order_rejected_not_raised_when_instrument_fetch_hits_network_error():
    broker, client = _manager(fake_client=_NetworkDownClient("testkey"))
    order = broker.place_future_order(_future_contract(), "LONG", num_lots=1, order_type=OrderType.MARKET)
    assert order.status == OrderStatus.REJECTED
    assert "ConnectionError" in order.reject_reason
    assert len(client.place_order_calls) == 0
