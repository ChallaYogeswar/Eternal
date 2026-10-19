import datetime as dt

import numpy as np
import pandas as pd
import pytest

from trading_risk_engine.data_provider import (
    MarketDataUnavailableError,
    SimulatedMarketDataProvider,
    YFinanceProvider,
)

REQUIRED_COLUMNS = {"open", "high", "low", "close", "volume"}


def test_simulated_provider_returns_correct_shape_and_columns():
    p = SimulatedMarketDataProvider(seed=1)
    df = p.get_daily_bars("TESTSYM.NS", dt.date(2025, 1, 1), dt.date(2025, 3, 1))
    assert REQUIRED_COLUMNS.issubset(set(df.columns))
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.is_monotonic_increasing
    assert len(df) > 0
    assert not df[list(REQUIRED_COLUMNS)].isna().any().any()


def test_simulated_provider_business_days_only():
    p = SimulatedMarketDataProvider(seed=1)
    df = p.get_daily_bars("TESTSYM.NS", dt.date(2026, 3, 1), dt.date(2026, 3, 31))
    weekdays = df.index.dayofweek  # Monday=0 ... Sunday=6
    assert (weekdays < 5).all(), "simulated market calendar must exclude Saturdays/Sundays"


def test_simulated_provider_ohlc_invariants_hold_every_bar():
    p = SimulatedMarketDataProvider(seed=7, annual_volatility_pct=0.60)  # high vol to stress the invariant
    df = p.get_daily_bars("STRESSTEST.NS", dt.date(2020, 1, 1), dt.date(2024, 1, 1))
    assert (df["high"] >= df["open"]).all()
    assert (df["high"] >= df["close"]).all()
    assert (df["high"] >= df["low"]).all()
    assert (df["low"] <= df["open"]).all()
    assert (df["low"] <= df["close"]).all()
    assert (df["volume"] > 0).all()
    assert (df["open"] > 0).all() and (df["close"] > 0).all()


def test_simulated_provider_is_deterministic_for_same_seed():
    p1 = SimulatedMarketDataProvider(seed=42)
    p2 = SimulatedMarketDataProvider(seed=42)
    df1 = p1.get_daily_bars("RELIANCE.NS", dt.date(2025, 6, 1), dt.date(2025, 9, 1))
    df2 = p2.get_daily_bars("RELIANCE.NS", dt.date(2025, 6, 1), dt.date(2025, 9, 1))
    pd.testing.assert_frame_equal(df1, df2)


def test_simulated_provider_different_symbols_are_not_identical():
    p = SimulatedMarketDataProvider(seed=42)
    df_a = p.get_daily_bars("AAA.NS", dt.date(2025, 6, 1), dt.date(2025, 9, 1))
    df_b = p.get_daily_bars("BBB.NS", dt.date(2025, 6, 1), dt.date(2025, 9, 1))
    assert not df_a["close"].equals(df_b["close"])


def test_simulated_provider_different_base_seeds_differ():
    p1 = SimulatedMarketDataProvider(seed=1)
    p2 = SimulatedMarketDataProvider(seed=2)
    df1 = p1.get_daily_bars("RELIANCE.NS", dt.date(2025, 6, 1), dt.date(2025, 9, 1))
    df2 = p2.get_daily_bars("RELIANCE.NS", dt.date(2025, 6, 1), dt.date(2025, 9, 1))
    assert not df1["close"].equals(df2["close"])


def test_simulated_provider_rejects_end_before_start():
    p = SimulatedMarketDataProvider()
    with pytest.raises(MarketDataUnavailableError):
        p.get_daily_bars("X.NS", dt.date(2025, 6, 1), dt.date(2025, 1, 1))


def test_simulated_provider_long_run_drift_is_in_the_right_direction():
    # Not a tight statistical test (that would make the suite flaky) —
    # just confirms strong positive drift over a long enough synthetic
    # window produces a materially higher ending price than starting
    # price, i.e. the mu-adjustment formula has the right sign.
    p = SimulatedMarketDataProvider(seed=99, annual_drift_pct=0.25, annual_volatility_pct=0.15, start_price=100.0)
    df = p.get_daily_bars("DRIFTTEST.NS", dt.date(2015, 1, 1), dt.date(2025, 1, 1))
    assert df["close"].iloc[-1] > df["close"].iloc[0] * 2.0


def test_simulated_get_latest_quote_shape_and_disclosure():
    p = SimulatedMarketDataProvider(seed=1)
    q = p.get_latest_quote("RELIANCE.NS")
    assert q["symbol"] == "RELIANCE.NS"
    assert q["price"] > 0
    assert "SIMULATED" in q["note"]


# ---------------------------------------------------------------------------
# The real yfinance seam — provider outages are simulated so these tests
# remain deterministic on runners with or without Yahoo Finance access.
# ---------------------------------------------------------------------------

def test_yfinance_provider_surfaces_download_failure(monkeypatch):
    import yfinance as yf

    def unavailable(*args, **kwargs):
        raise ConnectionError("simulated network outage")

    monkeypatch.setattr(yf, "download", unavailable)
    p = YFinanceProvider()
    with pytest.raises(MarketDataUnavailableError, match="ConnectionError"):
        p.get_daily_bars("RELIANCE.NS", dt.date(2025, 1, 1), dt.date(2025, 2, 1))


def test_yfinance_provider_surfaces_quote_failure(monkeypatch):
    import yfinance as yf

    class UnavailableTicker:
        @property
        def fast_info(self):
            raise ConnectionError("simulated network outage")

    monkeypatch.setattr(yf, "Ticker", lambda symbol: UnavailableTicker())
    p = YFinanceProvider()
    with pytest.raises(MarketDataUnavailableError, match="ConnectionError"):
        p.get_latest_quote("TCS.NS")


def test_yfinance_provider_missing_package_path_is_reachable(monkeypatch):
    """Exercises the ImportError branch without needing to actually
    uninstall yfinance — a legitimate way to cover a defensive code path
    that only fires in an environment configuration this sandbox doesn't
    itself have (yfinance genuinely absent)."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("simulated: yfinance not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    p = YFinanceProvider()
    with pytest.raises(MarketDataUnavailableError, match="not installed"):
        p.get_daily_bars("RELIANCE.NS", dt.date(2025, 1, 1), dt.date(2025, 2, 1))
