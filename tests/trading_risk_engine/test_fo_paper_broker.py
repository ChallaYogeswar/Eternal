import datetime as dt

import pytest

from trading_risk_engine.instruments.contracts import FutureContract, OptionContract, OptionType
from trading_risk_engine.instruments.fo_paper_broker import FOPaperBroker


def _broker(tmp_path, starting_cash=1_000_000.0) -> FOPaperBroker:
    return FOPaperBroker(str(tmp_path / "fo_paper.db"), starting_cash=starting_cash)


def _call():
    return OptionContract(underlying_symbol="NIFTY", strike=24500, expiry=dt.date(2026, 8, 18),
                          option_type=OptionType.CALL)


def _future():
    return FutureContract(underlying_symbol="NIFTY", expiry=dt.date(2026, 8, 18))


def test_long_option_open_costs_premium_times_quantity(tmp_path):
    broker = _broker(tmp_path)
    order = broker.open_option_position(_call(), "LONG", num_lots=1, premium=150.0, margin_required=9750.0,
                                        stop_loss_price=75.0, take_profit_price=300.0, entry_date="2026-08-10")
    assert order.status == "FILLED"
    assert broker.get_margins()["cash"] == pytest.approx(1_000_000.0 - 150.0 * 65)


def test_long_option_full_round_trip_pnl_matches_cash_change(tmp_path):
    broker = _broker(tmp_path)
    broker.open_option_position(_call(), "LONG", num_lots=1, premium=150.0, margin_required=9750.0,
                                stop_loss_price=75.0, take_profit_price=300.0, entry_date="2026-08-10")
    positions = broker.get_positions()
    assert len(positions) == 1
    pnl = broker.close_position(positions[0]["position_id"], exit_price=200.0)
    expected_pnl = (200.0 - 150.0) * 65
    assert pnl == pytest.approx(expected_pnl)
    final_cash = broker.get_margins()["cash"]
    assert (final_cash - 1_000_000.0) == pytest.approx(expected_pnl)
    assert broker.get_positions() == []


def test_long_option_rejected_for_insufficient_cash(tmp_path):
    broker = _broker(tmp_path, starting_cash=1_000.0)
    order = broker.open_option_position(_call(), "LONG", num_lots=1, premium=150.0, margin_required=9750.0,
                                        stop_loss_price=75.0, take_profit_price=300.0, entry_date="2026-08-10")
    assert order.status == "REJECTED"
    assert "insufficient cash" in order.reject_reason
    assert broker.get_positions() == []
    assert broker.get_margins()["cash"] == pytest.approx(1_000.0)


def test_short_option_open_credits_premium(tmp_path):
    broker = _broker(tmp_path)
    order = broker.open_option_position(_call(), "SHORT", num_lots=1, premium=150.0, margin_required=200_000.0,
                                        stop_loss_price=375.0, take_profit_price=75.0, entry_date="2026-08-10")
    assert order.status == "FILLED"
    assert broker.get_margins()["cash"] == pytest.approx(1_000_000.0 + 150.0 * 65)
    assert broker.get_margins()["margin_blocked"] == pytest.approx(200_000.0)


def test_short_option_full_round_trip_pnl_matches_cash_change(tmp_path):
    broker = _broker(tmp_path)
    broker.open_option_position(_call(), "SHORT", num_lots=1, premium=150.0, margin_required=200_000.0,
                                stop_loss_price=375.0, take_profit_price=75.0, entry_date="2026-08-10")
    positions = broker.get_positions()
    pnl = broker.close_position(positions[0]["position_id"], exit_price=100.0)
    expected_pnl = (150.0 - 100.0) * 65
    assert pnl == pytest.approx(expected_pnl)
    final_cash = broker.get_margins()["cash"]
    assert (final_cash - 1_000_000.0) == pytest.approx(expected_pnl)
    assert broker.get_margins()["margin_blocked"] == pytest.approx(0.0)


def test_short_option_rejected_for_insufficient_margin(tmp_path):
    broker = _broker(tmp_path, starting_cash=1_000.0)
    order = broker.open_option_position(_call(), "SHORT", num_lots=1, premium=150.0, margin_required=200_000.0,
                                        stop_loss_price=375.0, take_profit_price=75.0, entry_date="2026-08-10")
    assert order.status == "REJECTED"
    assert "insufficient margin" in order.reject_reason


def test_future_open_moves_no_cash_only_margin(tmp_path):
    broker = _broker(tmp_path)
    order = broker.open_future_position(_future(), "LONG", num_lots=1, price=24400.0, margin_required=190_320.0,
                                        stop_loss_price=24175.0, take_profit_price=24850.0, entry_date="2026-08-10")
    assert order.status == "FILLED"
    assert broker.get_margins()["cash"] == pytest.approx(1_000_000.0)
    assert broker.get_margins()["margin_blocked"] == pytest.approx(190_320.0)


def test_future_round_trip_pnl_matches_cash_change(tmp_path):
    broker = _broker(tmp_path)
    broker.open_future_position(_future(), "LONG", num_lots=1, price=24400.0, margin_required=190_320.0,
                                stop_loss_price=24175.0, take_profit_price=24850.0, entry_date="2026-08-10")
    positions = broker.get_positions()
    pnl = broker.close_position(positions[0]["position_id"], exit_price=24500.0)
    expected_pnl = (24500.0 - 24400.0) * 65
    assert pnl == pytest.approx(expected_pnl)
    final_cash = broker.get_margins()["cash"]
    assert (final_cash - 1_000_000.0) == pytest.approx(expected_pnl)
    assert broker.get_margins()["margin_blocked"] == pytest.approx(0.0)


def test_future_short_loss_reduces_cash(tmp_path):
    broker = _broker(tmp_path)
    broker.open_future_position(_future(), "SHORT", num_lots=1, price=24400.0, margin_required=190_320.0,
                                stop_loss_price=24625.0, take_profit_price=23950.0, entry_date="2026-08-10")
    positions = broker.get_positions()
    pnl = broker.close_position(positions[0]["position_id"], exit_price=24600.0)
    expected_pnl = (24400.0 - 24600.0) * 65
    assert pnl == pytest.approx(expected_pnl)
    assert pnl < 0
    final_cash = broker.get_margins()["cash"]
    assert (final_cash - 1_000_000.0) == pytest.approx(expected_pnl)


def test_state_persists_across_separate_broker_instances(tmp_path):
    db_path = str(tmp_path / "fo_paper.db")
    broker_a = FOPaperBroker(db_path, starting_cash=1_000_000.0)
    broker_a.open_future_position(_future(), "LONG", num_lots=1, price=24400.0, margin_required=190_320.0,
                                  stop_loss_price=24175.0, take_profit_price=24850.0, entry_date="2026-08-10")

    broker_b = FOPaperBroker(db_path)
    positions = broker_b.get_positions()
    assert len(positions) == 1
    assert positions[0]["underlying_symbol"] == "NIFTY"


def test_close_nonexistent_position_returns_none_not_a_crash(tmp_path):
    broker = _broker(tmp_path)
    assert broker.close_position("does-not-exist", exit_price=100.0) is None


@pytest.mark.parametrize("bad", ["200", "abc", None, 0, -5.0, float("nan"), float("inf"), True])
def test_close_position_rejects_non_numeric_or_non_positive_exit_price(tmp_path, bad):
    """Audit E-10: a bad exit price must fail loudly BEFORE touching cash or positions."""
    broker = _broker(tmp_path)
    broker.open_option_position(_call(), "LONG", num_lots=1, premium=150.0, margin_required=9750.0,
                                stop_loss_price=75.0, take_profit_price=300.0, entry_date="2026-08-10")
    pid = broker.get_positions()[0]["position_id"]
    cash_before = broker.get_margins()["cash"]
    with pytest.raises(ValueError, match="exit_price"):
        broker.close_position(pid, exit_price=bad)
    assert len(broker.get_positions()) == 1 and broker.get_margins()["cash"] == cash_before
