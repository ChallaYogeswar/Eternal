import os

import pytest

from trading_risk_engine.broker.paper_broker import PaperBroker
from trading_risk_engine.models import OrderSide, OrderStatus, OrderType


DB = "/tmp/phase6_paper_broker_test.db"


@pytest.fixture(autouse=True)
def _clean_db():
    if os.path.exists(DB):
        os.remove(DB)
    yield
    if os.path.exists(DB):
        os.remove(DB)


def _broker(**overrides):
    defaults = dict(db_path=DB, starting_cash=100_000.0, commission_pct=0.001, commission_flat=10.0, slippage_bps=10.0)
    defaults.update(overrides)
    return PaperBroker(**defaults)


def test_market_buy_hand_calculation():
    # fill = 200*(1.001) = 200.2 ; commission = 10 + 0.001*200.2*100 = 30.02
    # cost = 200.2*100 + 30.02 = 20050.02 ; cash = 100000-20050.02 = 79949.98
    b = _broker()
    order = b.place_order("TEST.NS", OrderSide.BUY, 100, OrderType.MARKET, reference_price=200.0)
    assert order.status == OrderStatus.FILLED
    assert order.fill_price == pytest.approx(200.2)
    assert order.commission == pytest.approx(30.02)
    assert b.get_margins()["cash"] == pytest.approx(79_949.98)
    positions = b.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == 100
    assert positions[0].entry_price == pytest.approx(200.2)


def test_market_sell_partial_hand_calculation():
    b = _broker()
    b.place_order("TEST.NS", OrderSide.BUY, 100, OrderType.MARKET, reference_price=200.0)
    cash_after_buy = b.get_margins()["cash"]

    # fill = 210*(0.999) = 209.79 ; commission = 10+0.001*209.79*50 = 20.4895
    # proceeds = 209.79*50 - 20.4895 = 10469.0105
    order = b.place_order("TEST.NS", OrderSide.SELL, 50, OrderType.MARKET, reference_price=210.0)
    assert order.status == OrderStatus.FILLED
    assert order.fill_price == pytest.approx(209.79)
    assert b.get_margins()["cash"] == pytest.approx(cash_after_buy + 10_469.0105)
    positions = b.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == 50


def test_selling_full_position_removes_it():
    b = _broker()
    b.place_order("TEST.NS", OrderSide.BUY, 100, OrderType.MARKET, reference_price=200.0)
    b.place_order("TEST.NS", OrderSide.SELL, 100, OrderType.MARKET, reference_price=210.0)
    assert b.get_positions() == []


def test_cannot_sell_more_than_held_no_shorting():
    b = _broker()
    b.place_order("TEST.NS", OrderSide.BUY, 100, OrderType.MARKET, reference_price=200.0)
    order = b.place_order("TEST.NS", OrderSide.SELL, 150, OrderType.MARKET, reference_price=210.0)
    assert order.status == OrderStatus.REJECTED
    assert "shorting" in order.reject_reason
    assert b.get_positions()[0].quantity == 100  # unchanged


def test_selling_with_zero_position_is_rejected():
    b = _broker()
    order = b.place_order("NEVERBOUGHT.NS", OrderSide.SELL, 10, OrderType.MARKET, reference_price=100.0)
    assert order.status == OrderStatus.REJECTED


def test_insufficient_cash_rejects_buy():
    b = _broker(starting_cash=1000.0)
    order = b.place_order("EXPENSIVE.NS", OrderSide.BUY, 100, OrderType.MARKET, reference_price=200.0)
    assert order.status == OrderStatus.REJECTED
    assert "insufficient" in order.reject_reason
    assert b.get_margins()["cash"] == pytest.approx(1000.0)  # untouched
    assert b.get_positions() == []


def test_market_order_without_reference_price_is_rejected_not_a_crash():
    b = _broker()
    order = b.place_order("TEST.NS", OrderSide.BUY, 10, OrderType.MARKET, reference_price=None)
    assert order.status == OrderStatus.REJECTED


def test_limit_buy_stays_pending_when_reference_above_limit():
    b = _broker(slippage_bps=0.0)
    order = b.place_order("TEST.NS", OrderSide.BUY, 10, OrderType.LIMIT, limit_price=100.0, reference_price=105.0)
    assert order.status == OrderStatus.PENDING
    assert b.get_positions() == []


def test_limit_buy_fills_at_the_more_favorable_price_when_it_crosses():
    b = _broker(slippage_bps=0.0, commission_pct=0.0, commission_flat=0.0)
    # reference is BELOW the limit -> a real limit buy fills at the better (lower) price
    order = b.place_order("TEST.NS", OrderSide.BUY, 10, OrderType.LIMIT, limit_price=100.0, reference_price=95.0)
    assert order.status == OrderStatus.FILLED
    assert order.fill_price == pytest.approx(95.0)  # the better of the two, not the limit itself


def test_limit_order_with_no_reference_price_stays_pending_for_later_evaluation():
    b = _broker()
    order = b.place_order("TEST.NS", OrderSide.BUY, 10, OrderType.LIMIT, limit_price=100.0, reference_price=None)
    assert order.status == OrderStatus.PENDING


def test_cancel_pending_order_succeeds_cancel_filled_order_fails():
    b = _broker()
    pending = b.place_order("TEST.NS", OrderSide.BUY, 10, OrderType.LIMIT, limit_price=50.0, reference_price=100.0)
    assert pending.status == OrderStatus.PENDING
    assert b.cancel_order(pending.order_id) is True
    assert b.get_order(pending.order_id).status == OrderStatus.CANCELLED

    filled = b.place_order("TEST.NS", OrderSide.BUY, 10, OrderType.MARKET, reference_price=100.0)
    assert b.cancel_order(filled.order_id) is False


def test_compute_equity_marks_to_supplied_prices():
    b = _broker(commission_pct=0.0, commission_flat=0.0, slippage_bps=0.0)
    b.place_order("AAA.NS", OrderSide.BUY, 100, OrderType.MARKET, reference_price=50.0)
    cash_left = b.get_margins()["cash"]
    equity = b.compute_equity({"AAA.NS": 55.0})
    assert equity == pytest.approx(cash_left + 100 * 55.0)


def test_compute_equity_falls_back_to_entry_price_for_unknown_symbol():
    b = _broker(commission_pct=0.0, commission_flat=0.0, slippage_bps=0.0)
    b.place_order("AAA.NS", OrderSide.BUY, 100, OrderType.MARKET, reference_price=50.0)
    equity = b.compute_equity({})  # no live price supplied at all
    assert equity == pytest.approx(b.get_margins()["cash"] + 100 * 50.0)


def test_state_persists_across_broker_instances_same_db_path():
    b1 = _broker()
    b1.place_order("TEST.NS", OrderSide.BUY, 100, OrderType.MARKET, reference_price=200.0)
    cash_after = b1.get_margins()["cash"]
    del b1

    b2 = PaperBroker(db_path=DB, starting_cash=999_999.0)  # starting_cash ignored on reopen
    assert b2.get_margins()["cash"] == pytest.approx(cash_after)
    assert len(b2.get_positions()) == 1


def test_averaging_into_an_existing_position_updates_volume_weighted_price():
    b = _broker(commission_pct=0.0, commission_flat=0.0, slippage_bps=0.0)
    b.place_order("TEST.NS", OrderSide.BUY, 100, OrderType.MARKET, reference_price=100.0)
    b.place_order("TEST.NS", OrderSide.BUY, 100, OrderType.MARKET, reference_price=200.0)
    # volume-weighted average: (100*100 + 100*200) / 200 = 150.0
    pos = b.get_positions()[0]
    assert pos.quantity == 200
    assert pos.entry_price == pytest.approx(150.0)


def test_rejects_non_positive_quantity():
    b = _broker()
    order = b.place_order("TEST.NS", OrderSide.BUY, 0, OrderType.MARKET, reference_price=100.0)
    assert order.status == OrderStatus.REJECTED
