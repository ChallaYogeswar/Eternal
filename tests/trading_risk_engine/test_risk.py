import datetime as dt

import pytest

from trading_risk_engine.config import Config
from trading_risk_engine.risk import CircuitBreakerStatus, DrawdownCircuitBreaker, RiskManager, compute_stop_and_target


def _config(**overrides) -> Config:
    defaults = dict(
        risk_per_trade_pct=0.01,
        atr_stop_multiplier=2.0,
        atr_target_multiplier=3.0,
        max_position_pct_of_equity=0.20,
        max_total_exposure_pct_of_equity=1.0,
        max_concurrent_positions=10,
        max_drawdown_circuit_breaker_pct=0.10,
        circuit_breaker_period="month",
        data_dir="/tmp/phase6_risk_test_data",
    )
    defaults.update(overrides)
    return Config(**defaults)


# ---------------------------------------------------------------------------
# Position sizing — hand-derived
# ---------------------------------------------------------------------------

def test_size_position_basic_no_caps_triggered():
    # equity=100000, risk 1% -> risk_amount=1000
    # entry=200, atr=5, stop_mult=2.0 -> stop_distance=10
    # shares = floor(1000/10) = 100
    rm = RiskManager(_config(max_position_pct_of_equity=0.5))
    d = rm.size_position("TEST", entry_price=200.0, atr_value=5.0, account_equity=100_000.0)
    assert d.shares == 100
    assert d.position_value == pytest.approx(20_000.0)
    assert d.stop_loss_price == pytest.approx(200.0 - 5.0 * 2.0)   # 190.0
    assert d.take_profit_price == pytest.approx(200.0 + 5.0 * 3.0)  # 215.0
    assert d.risk_amount == pytest.approx(1000.0)  # shares(100) * stop_distance(10) == budgeted risk exactly here


def test_size_position_capped_by_max_position_pct():
    # equity=100000, risk 1% -> risk_amount=1000
    # entry=500, atr=2, stop_mult=2.0 -> stop_distance=4 -> uncapped shares=250 -> value=125000
    # max_position_pct=0.20 -> max_position_value=20000 -> capped shares=floor(20000/500)=40
    rm = RiskManager(_config(max_position_pct_of_equity=0.20))
    d = rm.size_position("TEST", entry_price=500.0, atr_value=2.0, account_equity=100_000.0)
    assert d.shares == 40
    assert d.position_value == pytest.approx(20_000.0)
    assert any("max_position_pct_of_equity" in r for r in d.reasons)


def test_size_position_capped_by_remaining_exposure_room():
    # equity=100000, existing exposure=90000 -> room=10000 (max_total_exposure_pct=1.0)
    # entry=100, atr=2, stop_mult=2.0 -> stop_distance=4, risk_amount=1000 -> uncapped shares=250 -> value=25000
    # capped by room: shares=floor(10000/100)=100
    rm = RiskManager(_config(max_total_exposure_pct_of_equity=1.0, max_position_pct_of_equity=1.0))
    d = rm.size_position("TEST", entry_price=100.0, atr_value=2.0, account_equity=100_000.0,
                          current_exposure_value=90_000.0)
    assert d.shares == 100
    assert d.position_value == pytest.approx(10_000.0)


def test_size_position_zero_shares_when_risk_budget_too_small():
    # equity=1000, risk 1% -> risk_amount=10; entry=500, atr=20, stop_mult=2 -> stop_distance=40
    # shares = floor(10/40) = 0
    rm = RiskManager(_config())
    d = rm.size_position("TEST", entry_price=500.0, atr_value=20.0, account_equity=1_000.0)
    assert d.shares == 0
    assert any("no trade" in r for r in d.reasons)


def test_size_position_invalid_atr_returns_zero_shares_not_a_crash():
    rm = RiskManager(_config())
    d1 = rm.size_position("TEST", entry_price=100.0, atr_value=0.0, account_equity=100_000.0)
    d2 = rm.size_position("TEST", entry_price=100.0, atr_value=float("nan"), account_equity=100_000.0)
    d3 = rm.size_position("TEST", entry_price=100.0, atr_value=-5.0, account_equity=100_000.0)
    for d in (d1, d2, d3):
        assert d.shares == 0
        assert "ATR" in d.reasons[0]


def test_size_position_non_positive_price_or_equity_is_safe():
    rm = RiskManager(_config())
    assert rm.size_position("TEST", entry_price=0.0, atr_value=5.0, account_equity=100_000.0).shares == 0
    assert rm.size_position("TEST", entry_price=100.0, atr_value=5.0, account_equity=0.0).shares == 0


def test_compute_stop_and_target_matches_position_sizing():
    cfg = _config(atr_stop_multiplier=1.5, atr_target_multiplier=3.0)
    stop, target = compute_stop_and_target(100.0, 4.0, cfg)
    assert stop == pytest.approx(100.0 - 4.0 * 1.5)   # 94.0
    assert target == pytest.approx(100.0 + 4.0 * 3.0)  # 112.0


def test_can_open_new_position_respects_concurrent_cap():
    rm = RiskManager(_config(max_concurrent_positions=3))
    assert rm.can_open_new_position(2) is True
    assert rm.can_open_new_position(3) is False
    assert rm.can_open_new_position(4) is False


# ---------------------------------------------------------------------------
# Drawdown circuit breaker
# ---------------------------------------------------------------------------

def test_circuit_breaker_triggers_at_threshold():
    cb = DrawdownCircuitBreaker(_config(max_drawdown_circuit_breaker_pct=0.10, circuit_breaker_period="month"))
    status = cb.check(dt.date(2026, 3, 15), current_drawdown_pct=-0.12)
    assert status.halted is True
    assert status.newly_triggered is True
    assert status.period_key == "2026-03"
    # recovery_return_needed(0.12) = 0.12/0.88 = 0.136364 -> 13.6364%
    assert status.return_needed_to_recover_pct == pytest.approx(13.6364, rel=1e-4)


def test_circuit_breaker_does_not_trigger_within_tolerance():
    cb = DrawdownCircuitBreaker(_config(max_drawdown_circuit_breaker_pct=0.10))
    status = cb.check(dt.date(2026, 3, 15), current_drawdown_pct=-0.05)
    assert status.halted is False
    assert status.newly_triggered is False


def test_circuit_breaker_stays_halted_for_rest_of_same_period():
    cb = DrawdownCircuitBreaker(_config(max_drawdown_circuit_breaker_pct=0.10, circuit_breaker_period="month"))
    first = cb.check(dt.date(2026, 3, 5), current_drawdown_pct=-0.15)
    assert first.halted is True and first.newly_triggered is True

    # Drawdown improves later in the SAME month, but the breaker stays halted for the period regardless.
    second = cb.check(dt.date(2026, 3, 28), current_drawdown_pct=-0.02)
    assert second.halted is True
    assert second.newly_triggered is False


def test_circuit_breaker_rearms_when_period_rolls_over():
    cb = DrawdownCircuitBreaker(_config(max_drawdown_circuit_breaker_pct=0.10, circuit_breaker_period="month"))
    cb.check(dt.date(2026, 3, 5), current_drawdown_pct=-0.15)
    next_month = cb.check(dt.date(2026, 4, 1), current_drawdown_pct=-0.15)
    # Time-based re-arm (build plan's literal "for the period"): even
    # though the drawdown hasn't improved at all, the new period allows
    # entries again -> this check itself re-triggers since -0.15 still
    # breaches the threshold, but it is a FRESH trigger for April, not a
    # continuation of March's halt.
    assert next_month.newly_triggered is True
    assert next_month.period_key == "2026-04"


def test_circuit_breaker_weekly_period_key():
    cb = DrawdownCircuitBreaker(_config(max_drawdown_circuit_breaker_pct=0.10, circuit_breaker_period="week"))
    status = cb.check(dt.date(2026, 3, 16), current_drawdown_pct=-0.20)  # a Monday
    assert status.period_key.startswith("2026-W")


def test_circuit_breaker_manual_reset():
    cb = DrawdownCircuitBreaker(_config(max_drawdown_circuit_breaker_pct=0.10))
    cb.check(dt.date(2026, 3, 5), current_drawdown_pct=-0.15)
    cb.reset()
    status = cb.check(dt.date(2026, 3, 6), current_drawdown_pct=-0.15)
    assert status.newly_triggered is True  # treated as a fresh trigger after manual reset
