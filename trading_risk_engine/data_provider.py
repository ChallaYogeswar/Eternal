"""
Market data providers.

Eternal_Master_Build_Plan.md Section 7.2: "Data: yfinance (NSE .NS
tickers), NSE bhavcopy, or Kite Connect." This module defines one
interface (`MarketDataProvider`) and two implementations:

- `YFinanceProvider` — real, not simulated. It makes a genuine call
  against the `yfinance` package and surfaces download and quote failures
  as `MarketDataUnavailableError`.
- `SimulatedMarketDataProvider` — the honest stand-in used by every test,
  the narrative demo, and the backtester/paper-trader examples in this
  phase. It generates deterministic (seeded), statistically reasonable
  daily OHLCV data via geometric Brownian motion — enough to exercise
  every indicator, signal, sizing, and backtesting code path faithfully,
  but it is SYNTHETIC data, not a market data feed, and every place it's
  used says so.

Both implementations return the exact same shape: a pandas DataFrame
indexed by date with columns open/high/low/close/volume (lowercase,
always, so indicators.py and everything downstream never needs to know
which provider produced the data).
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd


class MarketDataUnavailableError(Exception):
    """Raised by any provider when data genuinely cannot be retrieved —
    never silently returns an empty or fabricated DataFrame instead."""


class MarketDataProvider(ABC):
    @abstractmethod
    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Returns a DataFrame indexed by date with columns
        open/high/low/close/volume, ascending by date. Raises
        MarketDataUnavailableError (never returns None, never returns a
        DataFrame with the wrong shape) if the range cannot be served."""

    @abstractmethod
    def get_latest_quote(self, symbol: str) -> dict:
        """Returns {'symbol', 'price', 'as_of'} at minimum."""


class YFinanceProvider(MarketDataProvider):
    """Real implementation. Requires the `yfinance` package (pip-
    installable) and, separately, live network access to Yahoo Finance's
    endpoints at call time. Tests simulate provider failures so their
    results do not depend on the runner's network access.
    """

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        try:
            import yfinance as yf
        except ImportError as e:
            raise MarketDataUnavailableError(
                "the 'yfinance' package is not installed — pip install yfinance"
            ) from e

        try:
            raw = yf.download(symbol, start=start.isoformat(), end=end.isoformat(),
                               progress=False, auto_adjust=True)
        except Exception as e:  # yfinance raises a variety of network/HTTP exception types
            raise MarketDataUnavailableError(
                f"could not fetch '{symbol}' from Yahoo Finance ({type(e).__name__}: {e}). "
                "If this is running in a network-restricted environment, this failure is "
                "expected — see this module's docstring."
            ) from e

        if raw is None or raw.empty:
            raise MarketDataUnavailableError(f"yfinance returned no data for '{symbol}' between {start} and {end}")

        # yfinance can return either a flat column index or a MultiIndex
        # (ticker, field) when called with auto_adjust — normalize both
        # to a flat lowercase open/high/low/close/volume frame.
        df = raw.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]

        required = ("open", "high", "low", "close", "volume")
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise MarketDataUnavailableError(f"yfinance response for '{symbol}' is missing columns: {missing}")

        return df[list(required)].sort_index()

    def get_latest_quote(self, symbol: str) -> dict:
        try:
            import yfinance as yf
        except ImportError as e:
            raise MarketDataUnavailableError("the 'yfinance' package is not installed") from e
        try:
            ticker = yf.Ticker(symbol)
            fast_info = ticker.fast_info
            price = float(fast_info["last_price"])
        except Exception as e:
            raise MarketDataUnavailableError(
                f"could not fetch a live quote for '{symbol}' ({type(e).__name__}: {e})"
            ) from e
        return {"symbol": symbol, "price": price, "as_of": pd.Timestamp.utcnow().isoformat()}


class SimulatedMarketDataProvider(MarketDataProvider):
    """Deterministic synthetic OHLCV via geometric Brownian motion.
    Same (symbol, start, end, seed) always produces the same bars —
    required for a reproducible backtest/demo, and for
    tests/test_backtester.py's ability to hand-verify a scenario's
    expected trades.

    Per-symbol series are independently seeded (base seed combined with
    a hash of the symbol) so a multi-symbol backtest doesn't get
    suspiciously correlated price paths just because they share a base
    seed.
    """

    def __init__(
        self,
        seed: int = 42,
        annual_drift_pct: float = 0.10,
        annual_volatility_pct: float = 0.25,
        start_price: float = 1000.0,
        overnight_gap_vol_pct: float = 0.004,
        intrabar_range_pct: float = 0.006,
    ):
        self.seed = seed
        self.annual_drift_pct = annual_drift_pct
        self.annual_volatility_pct = annual_volatility_pct
        self.start_price = start_price
        self.overnight_gap_vol_pct = overnight_gap_vol_pct
        self.intrabar_range_pct = intrabar_range_pct

    def _rng_for_symbol(self, symbol: str) -> np.random.Generator:
        symbol_hash = int(hashlib.sha256(symbol.encode("utf-8")).hexdigest(), 16) % (2**31 - 1)
        return np.random.default_rng(self.seed ^ symbol_hash)

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        if end < start:
            raise MarketDataUnavailableError(f"end date {end} is before start date {start}")

        dates = pd.bdate_range(start=start, end=end)  # business days — no weekends, matching real market calendars
        n = len(dates)
        if n == 0:
            raise MarketDataUnavailableError(f"no business days between {start} and {end}")

        rng = self._rng_for_symbol(symbol)
        trading_days_per_year = 252
        daily_sigma = self.annual_volatility_pct / np.sqrt(trading_days_per_year)
        # GBM drift term: subtracting 0.5*sigma^2 converts the ARITHMETIC
        # target annual_drift_pct into the correct log-return drift so
        # the simulated series' expected simple return matches
        # annual_drift_pct over time, not (annual_drift_pct + a variance
        # bias) — a detail that's easy to get quietly wrong.
        daily_mu = (self.annual_drift_pct - 0.5 * self.annual_volatility_pct ** 2) / trading_days_per_year

        log_returns = rng.normal(loc=daily_mu, scale=daily_sigma, size=n)
        closes = self.start_price * np.exp(np.cumsum(log_returns))

        prev_closes = np.concatenate([[self.start_price], closes[:-1]])
        gap_noise = rng.normal(loc=0.0, scale=self.overnight_gap_vol_pct, size=n)
        opens = prev_closes * (1.0 + gap_noise)

        intrabar_noise = np.abs(rng.normal(loc=0.0, scale=self.intrabar_range_pct, size=n))
        highs = np.maximum(opens, closes) * (1.0 + intrabar_noise)
        lows = np.minimum(opens, closes) * (1.0 - intrabar_noise)
        # A degenerate (zero-noise) draw could in principle put low
        # fractionally above the min(open,close) it's derived from once
        # floating point rounds it — guard the OHLC invariant explicitly
        # rather than trusting the arithmetic never rounds the wrong way.
        highs = np.maximum(highs, np.maximum(opens, closes))
        lows = np.minimum(lows, np.minimum(opens, closes))

        base_volume = 500_000.0
        volume_noise = np.exp(rng.normal(loc=0.0, scale=0.35, size=n))
        volumes = base_volume * volume_noise * (1.0 + 3.0 * np.abs(log_returns) / daily_sigma / 20.0)

        df = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
            index=pd.DatetimeIndex(dates, name="date"),
        )
        return df

    def get_latest_quote(self, symbol: str) -> dict:
        end = date.today()
        start = end - timedelta(days=10)
        bars = self.get_daily_bars(symbol, start, end)
        last = bars.iloc[-1]
        return {
            "symbol": symbol, "price": float(last["close"]),
            "as_of": bars.index[-1].isoformat(),
            "note": "SIMULATED price — not a real market quote (see trading_risk_engine/data_provider.py)",
        }
