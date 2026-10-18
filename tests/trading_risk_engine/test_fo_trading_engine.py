"""Repeatable tests for FOTradingEngine (mcp_servers/trading_engine/data.py).

Converts the manual smoke test from the Phase 6 Extended build into a
checked-in test file. Exercises the engine directly (no gateway / MCP wire),
so it needs only trading_risk_engine/ and mcp_servers/ — no qap_p/rcap_p/phase4/phase5.
Wire-level behaviour is still proven only by the e2e scripts.
"""
import importlib.util
import os
import sys

import pytest

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "mcp_servers", "trading_engine", "data.py")


@pytest.fixture
def data_mod(tmp_path, monkeypatch):
    monkeypatch.setenv("PHASE6_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    sys.modules.pop("data", None)  # same name-collision guard the project uses elsewhere
    spec = importlib.util.spec_from_file_location("data", _DATA_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["data"] = mod
    spec.loader.exec_module(mod)
    mod.reset_engine_for_tests()
    mod.reset_fo_engine_for_tests()
    yield mod
    mod.reset_fo_engine_for_tests()
    mod.reset_engine_for_tests()
    sys.modules.pop("data", None)


def test_option_chain_has_full_ce_and_pe(data_mod):
    chain = data_mod.get_fo_engine().get_option_chain("NIFTY", "WEEKLY", 3)
    assert len(chain["quotes"]) == (3 * 2 + 1) * 2
    assert {q["option_type"] for q in chain["quotes"]} == {"CE", "PE"}


def test_signal_has_valid_direction(data_mod):
    sig = data_mod.get_fo_engine().get_signal("NIFTY", 400)
    assert sig["direction"] in ("BUY", "SELL", "HOLD")


def test_options_backtest_runs_and_is_json_safe(data_mod):
    bt = data_mod.get_fo_engine().run_options_backtest("NIFTY", "DIRECTIONAL_LONG", "WEEKLY", 200, 1_000_000.0)
    assert bt["summary"]["num_trades"] >= 0
    assert type(bt["total_return_pct"]) is float  # no numpy leakage into MCP JSON


def test_futures_backtest_runs(data_mod):
    bt = data_mod.get_fo_engine().run_futures_backtest("NIFTY", 200, 1_000_000.0)
    assert bt["starting_equity"] == 1_000_000.0


def test_paper_option_and_future_round_trip(data_mod):
    engine = data_mod.get_fo_engine()
    chain = engine.get_option_chain("NIFTY", "WEEKLY", 3)
    strike = chain["quotes"][3]["strike"]
    opt = engine.place_paper_option_order("NIFTY", strike, "WEEKLY", "CE", "LONG", 1)
    fut = engine.place_paper_future_order("NIFTY", "LONG", 1)
    assert opt["status"] == "FILLED" and fut["status"] == "FILLED"
    positions = engine.get_paper_positions()
    assert {p["kind"] for p in positions} == {"OPTION", "FUTURE"}
    assert engine.get_paper_margins()["margin_blocked"] > 0
    for p in positions:
        closed = engine.close_paper_position(p["position_id"])
        assert closed["position_id"] == p["position_id"]
    assert engine.get_paper_positions() == []


@pytest.mark.parametrize("call,args", [
    ("get_option_chain", ("BANKNIFTY", "WEEKLY", 2)),   # monthly-only underlying
    ("get_signal", ("GOLD", 100)),                        # unknown underlying
    ("place_paper_option_order", ("NIFTY", 24000, "WEEKLY", "XX", "LONG", 1)),
    ("place_paper_option_order", ("NIFTY", 24000, "WEEKLY", "CE", "SIDEWAYS", 1)),
    ("place_paper_option_order", ("NIFTY", 24000, "WEEKLY", "CE", "LONG", 0)),
    ("close_paper_position", ("does-not-exist",)),
])
def test_bad_inputs_raise_clean_errors(data_mod, call, args):
    with pytest.raises(data_mod.TradingEngineError):
        getattr(data_mod.get_fo_engine(), call)(*args)


def test_live_orders_refused_when_not_configured(data_mod):
    engine = data_mod.get_fo_engine()
    with pytest.raises(data_mod.TradingEngineError, match="not configured"):
        engine.place_live_option_order("NIFTY", 24000, "WEEKLY", "CE", "LONG", 1)
    with pytest.raises(data_mod.TradingEngineError, match="not configured"):
        engine.place_live_future_order("NIFTY", "LONG", 1)
