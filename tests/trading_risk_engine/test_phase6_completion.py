"""Tests for the Phase 6 completion items: 2025 calendar, F&O get_order(), F&O MARKET-order protection,
F&O performance metrics, and the go-live gate."""
import datetime as dt
import json
import math
import os

import pytest

from trading_risk_engine.go_live import GoLiveCriteria, evaluate_go_live, t_test_mean_positive
from trading_risk_engine.instruments.calendar import ExpiryResolver, TradingCalendar
from trading_risk_engine.instruments.fo_backtester import FOEquityPoint, FOTrade
from trading_risk_engine.instruments.fo_performance import PROFIT_FACTOR_CAP, build_fo_performance
from trading_risk_engine.instruments.underlyings import get_underlying
from trading_risk_engine.models import OrderSide, OrderStatus, OrderType

from tests.trading_risk_engine.test_fo_kite_connect_broker import (  # reuse the verified fake Kite client
    FakeFOKiteClient, _call_contract, _future_contract, _manager,
)


# ---- 2025 calendar ----------------------------------------------------------------------------
def test_2025_holidays_loaded_and_trading_days_resolve():
    cal = TradingCalendar()
    assert not cal.is_trading_day(dt.date(2025, 10, 22))   # Balipratipada
    assert not cal.is_trading_day(dt.date(2025, 4, 18))    # Good Friday
    assert cal.is_trading_day(dt.date(2025, 1, 1))         # equity traded on New Year's Day
    assert cal.is_trading_day(dt.date(2025, 10, 23))


def test_weekly_expiry_in_2025_shifts_off_a_real_holiday():
    # Oct 21 2025 (a Tuesday) is a holiday -> NIFTY's Tuesday weekly expiry shifts to Monday Oct 20.
    exp = ExpiryResolver().next_weekly_expiry(get_underlying("NIFTY"), dt.date(2025, 10, 20))
    assert exp == dt.date(2025, 10, 20)
    exp2 = ExpiryResolver().next_weekly_expiry(get_underlying("NIFTY"), dt.date(2025, 10, 21))
    assert exp2 == dt.date(2025, 10, 20) or exp2 > dt.date(2025, 10, 21)  # never lands on the holiday
    assert TradingCalendar().is_trading_day(exp2)


def test_years_without_data_still_fail_closed():
    with pytest.raises(ValueError):
        TradingCalendar().is_trading_day(dt.date(2024, 6, 3))


# ---- F&O get_order() --------------------------------------------------------------------------
class _ClientWithHistory(FakeFOKiteClient):
    def __init__(self, api_key):
        super().__init__(api_key)
        self._hist = {}

    def order_history(self, order_id):
        if order_id == "BOOM":
            raise RuntimeError("network down")
        return self._hist.get(order_id, [])


def test_fo_get_order_maps_status_and_fields():
    client = _ClientWithHistory("testkey")
    client._hist["OID1"] = [
        {"status": "OPEN", "tradingsymbol": "NIFTY26818CE", "transaction_type": "BUY", "quantity": 65,
         "order_type": "LIMIT", "price": 150.0, "average_price": 0.0},
        {"status": "COMPLETE", "tradingsymbol": "NIFTY26818CE", "transaction_type": "BUY", "quantity": 65,
         "order_type": "LIMIT", "price": 150.0, "average_price": 149.5, "status_message": None},
    ]
    broker, _ = _manager(fake_client=client)
    o = broker.get_order("OID1")
    assert o.status == OrderStatus.FILLED and o.fill_price == 149.5
    assert o.side == OrderSide.BUY and o.quantity == 65 and o.symbol == "NIFTY26818CE"


def test_fo_get_order_unknown_error_and_unauthenticated_return_none():
    client = _ClientWithHistory("testkey")
    broker, _ = _manager(fake_client=client)
    assert broker.get_order("NEVER") is None
    assert broker.get_order("BOOM") is None
    broker_noauth, _ = _manager(token=None, fake_client=client)
    assert broker_noauth.get_order("OID1") is None


def test_fo_get_order_pending_and_rejected_states():
    client = _ClientWithHistory("testkey")
    client._hist["P"] = [{"status": "TRIGGER PENDING", "transaction_type": "SELL", "quantity": 20, "order_type": "MARKET"}]
    client._hist["R"] = [{"status": "REJECTED", "transaction_type": "BUY", "quantity": 20, "order_type": "LIMIT",
                          "status_message": "margin"}]
    broker, _ = _manager(fake_client=client)
    assert broker.get_order("P").status == OrderStatus.PENDING
    r = broker.get_order("R")
    assert r.status == OrderStatus.REJECTED and r.reject_reason == "margin"


# ---- F&O MARKET-order protection --------------------------------------------------------------
@pytest.mark.parametrize("pct", [None, 0, -1.0])
def test_fo_market_order_without_positive_protection_is_rejected_and_never_sent(pct):
    broker, client = _manager()
    for o in (broker.place_future_order(_future_contract(), "LONG", 1, OrderType.MARKET, market_protection_pct=pct),
              broker.place_option_order(_call_contract(), "LONG", 1, OrderType.MARKET, market_protection_pct=pct)):
        assert o.status == OrderStatus.REJECTED and "market_protection_pct" in o.reject_reason
    assert client.place_order_calls == []


def test_fo_market_order_with_protection_sends_market_protection_kwarg():
    broker, client = _manager()
    o = broker.place_option_order(_call_contract(), "SHORT", 1, OrderType.MARKET, market_protection_pct=2.5)
    assert o.status == OrderStatus.PENDING
    assert client.place_order_calls[0]["market_protection"] == 2.5


def test_fo_limit_order_unaffected_by_protection_rule():
    broker, client = _manager()
    o = broker.place_future_order(_future_contract(), "LONG", 1, OrderType.LIMIT, limit_price=24000.0)
    assert o.status == OrderStatus.PENDING and "market_protection" not in client.place_order_calls[0]


# ---- F&O performance --------------------------------------------------------------------------
def _curve(vals):
    return [FOEquityPoint(date=f"2026-01-{i+1:02d}", cash=v, margin_blocked=0.0, mark_to_market_value=0.0, equity=v)
            for i, v in enumerate(vals)]


def _trade(pnl, risk=100.0):
    return FOTrade(contract=None, side="LONG", num_lots=1, entry_date="a", exit_date="b", entry_price=1.0,
                   exit_price=1.0, exit_reason="x", margin_required=0.0, risk_amount=risk, pnl=pnl)


def test_fo_performance_known_values():
    rep = build_fo_performance(_curve([100, 110, 99, 120]), [_trade(200), _trade(-100), _trade(100)],
                               annual_risk_free_pct=0.0)
    assert rep["total_return_pct"] == pytest.approx(20.0)
    assert rep["max_drawdown_pct"] == pytest.approx(-10.0)       # 110 -> 99; negative, same as the equity report
    assert rep["profit_factor"] == pytest.approx(3.0)            # 300 / 100
    assert rep["expectancy_r"] == pytest.approx((2 - 1 + 1) / 3, abs=1e-4)
    json.dumps(rep, allow_nan=False)


def test_fo_performance_no_losers_is_capped_not_inf_and_json_safe():
    rep = build_fo_performance(_curve([100, 101, 102]), [_trade(50), _trade(10)], annual_risk_free_pct=0.065)
    assert rep["profit_factor"] == PROFIT_FACTOR_CAP
    json.dumps(rep, allow_nan=False)


def test_fo_performance_zero_trades_and_flat_curve_are_safe():
    rep = build_fo_performance(_curve([100, 100, 100]), [], annual_risk_free_pct=0.065)
    assert rep["sharpe_ratio"] == 0.0 and rep["profit_factor"] == 0.0 and rep["kelly_fraction"] is None
    json.dumps(rep, allow_nan=False)
    with pytest.raises(ValueError):
        build_fo_performance([], [], annual_risk_free_pct=0.0)


def test_engine_backtest_result_includes_performance(tmp_path, monkeypatch):
    import importlib.util, sys
    path = os.path.join(os.path.dirname(__file__), "..", "..", "mcp_servers", "trading_engine", "data.py")
    monkeypatch.setenv("PHASE6_DATA_DIR", str(tmp_path))
    sys.modules.pop("data", None)
    spec = importlib.util.spec_from_file_location("data", path)
    mod = importlib.util.module_from_spec(spec); sys.modules["data"] = mod; spec.loader.exec_module(mod)
    try:
        mod.reset_engine_for_tests(); mod.reset_fo_engine_for_tests()
        r = mod.get_fo_engine().run_options_backtest("NIFTY", "DIRECTIONAL_LONG", "WEEKLY", 120)
        assert "sharpe_ratio" in r["performance"] and "max_drawdown_pct" in r["performance"]
        json.dumps(r, allow_nan=False)
    finally:
        mod.reset_fo_engine_for_tests(); mod.reset_engine_for_tests(); sys.modules.pop("data", None)


# ---- go-live gate -----------------------------------------------------------------------------
def _good(n, mean=0.4, sd=1.0):
    # deterministic, alternating around `mean` with std ~ sd
    return [mean + (sd if i % 2 else -sd) * 0.9 for i in range(n)]


def _eq():
    return [100, 103, 101, 106, 110]


def test_go_live_passes_when_all_evidence_clears():
    rep = evaluate_go_live(_good(120), _good(40), 35, _eq())
    assert rep.ready, [c for c in rep.checks if not c.passed]
    assert rep.p_value < 0.05 and rep.mean_r > 0


@pytest.mark.parametrize("kwargs,failed", [
    (dict(backtest_r_multiples=_good(50), paper_r_multiples=_good(40), paper_trading_days=35), "backtest_sample_size"),
    (dict(backtest_r_multiples=_good(120), paper_r_multiples=_good(5), paper_trading_days=35), "paper_sample_size"),
    (dict(backtest_r_multiples=_good(120), paper_r_multiples=_good(40), paper_trading_days=7), "paper_duration"),
    (dict(backtest_r_multiples=_good(120, mean=-0.2), paper_r_multiples=_good(40, mean=-0.2), paper_trading_days=35), "statistical_edge"),
    (dict(backtest_r_multiples=_good(120), paper_r_multiples=_good(40, mean=-0.3), paper_trading_days=35), "paper_agrees_with_backtest"),
])
def test_go_live_each_check_can_block(kwargs, failed):
    rep = evaluate_go_live(equity_values=_eq(), **kwargs)
    assert not rep.ready
    assert failed in [c.name for c in rep.checks if not c.passed]


def test_go_live_fail_closed_on_missing_equity_curve_and_drawdown_limit():
    assert not evaluate_go_live(_good(120), _good(40), 35, None).ready
    rep = evaluate_go_live(_good(120), _good(40), 35, [100, 70, 90])
    assert "max_drawdown" in [c.name for c in rep.checks if not c.passed]


def test_go_live_empty_inputs_fail_without_crashing():
    rep = evaluate_go_live([], [], 0, None)
    assert not rep.ready and len(rep.checks) >= 6


def test_t_test_edge_cases():
    assert t_test_mean_positive([]) == (0.0, 0.0, 1.0)
    assert t_test_mean_positive([1.0]) [2] == 1.0
    m, t, p = t_test_mean_positive([1.0, 1.0, 1.0])
    assert m == 1.0 and math.isinf(t) and p == 0.0
    assert t_test_mean_positive([-1.0, -1.0])[2] == 1.0


def test_custom_criteria_are_respected():
    lax = GoLiveCriteria(min_backtest_trades=10, min_paper_trades=5, min_paper_trading_days=1)
    assert evaluate_go_live(_good(12), _good(6), 2, _eq(), lax).ready
