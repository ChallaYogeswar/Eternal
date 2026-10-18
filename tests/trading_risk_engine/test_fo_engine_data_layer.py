"""Data-layer tests for mcp_servers/trading_engine/data.py's FOTradingEngine (the MCP tool backend).

These exercise the engine directly (below the gateway/MCP wire, which scripts/trading_risk_engine/
{mcp_e2e,http_e2e}.py cover). Every result is also pushed through json.dumps — the failure class
(numpy leakage, non-serializable fields) that only shows up on the wire in production.
"""
import importlib.util
import json
import pathlib
import sys

import pytest

from trading_risk_engine.config import Config as RealConfig

_DATA_PY = pathlib.Path(__file__).resolve().parents[2] / "mcp_servers" / "trading_engine" / "data.py"


@pytest.fixture
def fo(tmp_path, monkeypatch):
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    monkeypatch.delenv("KITE_API_SECRET", raising=False)
    spec = importlib.util.spec_from_file_location("trading_engine_data_under_test", _DATA_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules["trading_engine_data_under_test"] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "Config", lambda: RealConfig(data_dir=str(tmp_path)))
    module.reset_engine_for_tests()
    module.reset_fo_engine_for_tests()
    yield module.get_fo_engine(), module
    sys.modules.pop("trading_engine_data_under_test", None)


def test_option_chain_is_json_serializable_and_put_call_parity_shaped(fo):
    engine, _ = fo
    chain = engine.get_option_chain("NIFTY", "WEEKLY", 3)
    json.dumps(chain)
    assert chain["underlying_symbol"] == "NIFTY"
    assert len(chain["quotes"]) == 2 * (2 * 3 + 1)


def test_signal_for_every_supported_underlying(fo):
    engine, _ = fo
    for u in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]:
        sig = engine.get_signal(u)
        json.dumps(sig)
        assert sig["direction"] in ("BUY", "SELL", "HOLD")


def test_unknown_underlying_is_a_clean_error(fo):
    engine, module = fo
    with pytest.raises(module.TradingEngineError):
        engine.get_signal("RELIANCE")


def test_futures_backtest_with_trades_serializes_and_has_r_multiples(fo):
    # Regression for the corrupted FOTrade.r_multiple attribute: needs a run that actually trades.
    engine, _ = fo
    result = engine.run_futures_backtest("SENSEX", 250, 5_000_000.0)
    json.dumps(result)
    assert len(result["trades"]) > 0
    assert all("r_multiple" in t for t in result["trades"])


def test_futures_backtest_refuses_oversized_lots_at_small_capital(fo):
    # At INR 10L one lot's ATR-stop risk exceeds the 1% budget for EVERY supported index, so the risk
    # model correctly sizes to zero lots (independently re-verified across all five underlyings).
    engine, _ = fo
    for u in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]:
        assert engine.run_futures_backtest(u, 250, 1_000_000.0)["trades"] == []


def test_options_backtest_trades_and_serializes(fo):
    engine, _ = fo
    result = engine.run_options_backtest("NIFTY", "DIRECTIONAL_LONG", "WEEKLY", 250, 1_000_000.0)
    json.dumps(result)
    assert len(result["trades"]) > 0


def test_paper_option_order_then_close_roundtrip(fo):
    engine, _ = fo
    chain = engine.get_option_chain("NIFTY", "WEEKLY", 2)
    strike = chain["quotes"][0]["strike"]
    placed = engine.place_paper_option_order("NIFTY", strike, "WEEKLY", "CE", "LONG", 1)
    json.dumps(placed)
    positions = engine.get_paper_positions()
    assert len(positions) == 1
    closed = engine.close_paper_position(positions[0]["position_id"])
    json.dumps(closed)
    assert engine.get_paper_positions() == []


def test_live_orders_refuse_when_live_trading_is_not_configured(fo):
    engine, module = fo
    with pytest.raises(module.TradingEngineError):
        engine.place_live_option_order("NIFTY", 24500.0, "WEEKLY", "CE", "LONG", 1)
    with pytest.raises(module.TradingEngineError):
        engine.place_live_future_order("NIFTY", "LONG", 1)
