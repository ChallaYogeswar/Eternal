"""
F&O backtester.

Deliberately a SEPARATE engine from `trading_risk_engine/backtester.py`, not a
retrofit of it — same reasoning as `fo_config.py`'s own docstring: F&O
positions don't have a share count or an ATR-dollar stop, they have a
lot count and (for options) a premium that decays with time even if the
underlying doesn't move at all. Retrofitting that into the equity engine
would mean scattering `if is_option:` branches through code that's
already correct and tested for its actual job; a dedicated engine keeps
both correct and both readable.

What IS reused, deliberately, per this project's own "instrument
adapter" principle: the directional signal itself. `indicators.py` and
`signals.SignalEngine` don't know or care whether the price series they
're given is a stock or an index — an index's own simulated OHLCV path
(from `FOMarketDataProvider.get_underlying_daily_bars`) runs through the
EXACT SAME signal engine equity Phase 6 already built and tested. This
module's only new job is translating "the index looks bullish/bearish"
into an options or futures ACTION, and then simulating that action's
own, very different, P&L mechanics.

No-lookahead discipline, carried over from the equity backtester: a
signal is decided from data through day T's close; the resulting
option/future position is priced (and filled) using day T+1's
UNDERLYING OPEN as the spot input to Black-Scholes / cost-of-carry — not
day T+1's close, which wouldn't be known yet when the fill actually
happens. An already-open position is marked to market daily using that
day's underlying CLOSE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type
from enum import Enum
from typing import Callable, List, Optional

import pandas as pd

from .. import indicators as ind
from ..config import Config as EquityConfig
from ..models import SignalDirection
from ..signals import SignalEngine
from .calendar import ExpiryResolver
from .contracts import FOPosition, FutureContract, OptionContract, OptionType
from .fo_config import FOConfig
from .fo_data_provider import FOMarketDataProvider
from .fo_risk import FORiskManager
from .margin import MarginConfig
from .underlyings import get_underlying


class OptionStrategyStyle(str, Enum):
    DIRECTIONAL_LONG = "DIRECTIONAL_LONG"  # bullish -> buy calls, bearish -> buy puts (bounded risk, pays theta)
    INCOME_SHORT = "INCOME_SHORT"  # bullish -> sell puts, bearish -> sell calls (collects theta, unbounded-ish risk)


class ExpiryChoice(str, Enum):
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


@dataclass
class FOTrade:
    contract: object
    side: str
    num_lots: int
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    exit_reason: str
    margin_required: float
    risk_amount: float
    pnl: float

    @property
    def r_multiple(self) -> float:
        return self.pnl / self.risk_amount if self.risk_amount > 0 else 0.0


@dataclass
class FOEquityPoint:
    date: str
    cash: float
    margin_blocked: float
    mark_to_market_value: float  # unrealized P&L on any open position, marked at today's close
    equity: float  # cash + mark_to_market_value (margin_blocked is committed, not spent — see report)


@dataclass
class FOBacktestReport:
    underlying_symbol: str
    strategy_style: Optional[str]
    trades: List[FOTrade]
    equity_curve: List[FOEquityPoint]
    starting_equity: float
    ending_equity: float
    total_return_pct: float


class FOBacktester:
    def __init__(
        self, fo_config: FOConfig, equity_config: Optional[EquityConfig] = None,
        margin_config: Optional[MarginConfig] = None, signal_engine: Optional[SignalEngine] = None,
    ):
        self.fo_config = fo_config
        self.equity_config = equity_config or EquityConfig(data_dir="/tmp/fo_backtester_default_data")
        self.risk_manager = FORiskManager(fo_config, margin_config)
        self.signal_engine = signal_engine or SignalEngine(self.equity_config)
        self.expiry_resolver = ExpiryResolver()

    def _signal_for_day(self, indicator_df: pd.DataFrame, t: int) -> SignalDirection:
        return self.signal_engine.generate("UNDERLYING", indicator_df.iloc[: t + 1]).direction

    def run_option_strategy(
        self, underlying_symbol: str, underlying_bars: pd.DataFrame, fo_provider: FOMarketDataProvider,
        strategy_style: OptionStrategyStyle, expiry_choice: ExpiryChoice = ExpiryChoice.WEEKLY,
        starting_cash: float = 1_000_000.0,
    ) -> FOBacktestReport:
        underlying = get_underlying(underlying_symbol)
        if expiry_choice == ExpiryChoice.WEEKLY and not underlying.has_weekly_options:
            raise ValueError(
                f"{underlying_symbol} has no weekly options contract (see underlyings.py) — "
                "use ExpiryChoice.MONTHLY for this underlying"
            )

        indicator_df = ind.compute_all(
            underlying_bars, atr_period=self.equity_config.atr_period, rsi_period=self.equity_config.rsi_period,
            bb_period=self.equity_config.bb_period, sma_trend_period=self.equity_config.sma_trend_period,
        )
        n = len(indicator_df)
        if n < 2:
            raise ValueError("need at least 2 bars to run an options backtest")

        cash = starting_cash
        margin_blocked = 0.0
        open_position: Optional[FOPosition] = None
        trades: List[FOTrade] = []

        first_date = indicator_df.index[0]
        equity_curve: List[FOEquityPoint] = [FOEquityPoint(
            date=first_date.date().isoformat(), cash=cash, margin_blocked=0.0,
            mark_to_market_value=0.0, equity=cash,
        )]

        for t in range(0, n - 1):
            next_idx = t + 1
            today = indicator_df.index[t].date()
            tomorrow = indicator_df.index[next_idx].date()
            tomorrow_open = float(indicator_df["open"].iloc[next_idx])
            tomorrow_close = float(indicator_df["close"].iloc[next_idx])

            # ---- 1. Mark-to-market / exit check for an already-open position ----
            if open_position is not None:
                contract: OptionContract = open_position.contract
                if contract.is_expired(tomorrow):
                    # Settled at intrinsic value — the option has no more time value left.
                    exit_quote = fo_provider.price_option(contract, tomorrow, tomorrow_open)
                    exit_price = exit_quote.theoretical_price
                    self._close_position(open_position, exit_price, tomorrow.isoformat(), "EXPIRY_SETTLEMENT",
                                         trades)
                    cash += self._realized_cash_delta(open_position, exit_price)
                    margin_blocked -= open_position.margin_blocked
                    open_position = None
                else:
                    quote = fo_provider.price_option(contract, tomorrow, tomorrow_open)
                    current_price = quote.theoretical_price
                    hit_stop = (
                        current_price <= open_position.stop_loss_price if open_position.side == "LONG"
                        else current_price >= open_position.stop_loss_price
                    )
                    hit_target = (
                        current_price >= open_position.take_profit_price if open_position.side == "LONG"
                        else current_price <= open_position.take_profit_price
                    )
                    if hit_stop or hit_target:
                        reason = "STOP_LOSS" if hit_stop else "TAKE_PROFIT"
                        self._close_position(open_position, current_price, tomorrow.isoformat(), reason, trades)
                        cash += self._realized_cash_delta(open_position, current_price)
                        margin_blocked -= open_position.margin_blocked
                        open_position = None

            # ---- 2. New entry, only if flat ----
            if open_position is None:
                signal = self._signal_for_day(indicator_df, t)
                action = self._translate_signal_to_option_action(signal, strategy_style)
                if action is not None:
                    option_type, side = action
                    today_close_spot = float(indicator_df["close"].iloc[t])
                    expiry = (
                        self.expiry_resolver.next_weekly_expiry(underlying, today)
                        if expiry_choice == ExpiryChoice.WEEKLY
                        else self.expiry_resolver.next_monthly_expiry(underlying, today)
                    )
                    strike = self._select_atm_strike(underlying_symbol, today_close_spot)
                    contract = OptionContract(underlying_symbol=underlying_symbol, strike=strike,
                                              expiry=expiry, option_type=option_type)
                    decision_premium = fo_provider.price_option(contract, today, today_close_spot).theoretical_price

                    equity_now = equity_curve[-1].equity
                    size_fn = self.risk_manager.size_long_option if side == "LONG" else self.risk_manager.size_short_option
                    decision = size_fn(contract, decision_premium, today_close_spot, equity_now, margin_blocked)

                    if decision.num_lots > 0:
                        fill_quote = fo_provider.price_option(contract, tomorrow, tomorrow_open)
                        fill_price = fill_quote.theoretical_price
                        margin_for_fill = decision.margin_required  # already computed for decision.num_lots
                        cash += (-fill_price if side == "LONG" else fill_price) * decision.num_lots * contract.lot_size
                        margin_blocked += margin_for_fill
                        open_position = FOPosition(
                            contract=contract, side=side, num_lots=decision.num_lots, entry_price=fill_price,
                            entry_date=tomorrow.isoformat(), margin_blocked=margin_for_fill,
                            stop_loss_price=self._reanchor_stop(side, fill_price, decision),
                            take_profit_price=self._reanchor_target(side, fill_price, decision),
                        )

            # ---- 3. Equity curve point, marked at tomorrow's CLOSE ----
            mtm_value = 0.0
            if open_position is not None:
                quote = fo_provider.price_option(open_position.contract, tomorrow, tomorrow_close)
                unrealized = (quote.theoretical_price - open_position.entry_price) * open_position.signed_quantity
                mtm_value = unrealized
            equity = cash + mtm_value
            equity_curve.append(FOEquityPoint(
                date=tomorrow.isoformat(), cash=cash, margin_blocked=margin_blocked,
                mark_to_market_value=mtm_value, equity=equity,
            ))

        if open_position is not None:
            last_date = indicator_df.index[-1].date()
            last_spot = float(indicator_df["close"].iloc[-1])
            exit_quote = fo_provider.price_option(open_position.contract, last_date, last_spot)
            self._close_position(open_position, exit_quote.theoretical_price, last_date.isoformat(),
                                 "END_OF_BACKTEST", trades)
            cash += self._realized_cash_delta(open_position, exit_quote.theoretical_price)
            equity_curve[-1] = FOEquityPoint(date=equity_curve[-1].date, cash=cash, margin_blocked=0.0,
                                             mark_to_market_value=0.0, equity=cash)

        ending_equity = equity_curve[-1].equity
        return FOBacktestReport(
            underlying_symbol=underlying_symbol, strategy_style=strategy_style.value, trades=trades,
            equity_curve=equity_curve, starting_equity=starting_cash, ending_equity=ending_equity,
            total_return_pct=((ending_equity / starting_cash) - 1.0) * 100.0 if starting_cash > 0 else 0.0,
        )

    def run_futures_strategy(
        self, underlying_symbol: str, underlying_bars: pd.DataFrame, fo_provider: FOMarketDataProvider,
        starting_cash: float = 1_000_000.0,
    ) -> FOBacktestReport:
        """Index futures always trade a monthly cycle — never weekly, for
        any index (verified while building calendar.py) — so unlike
        `run_option_strategy` there's no expiry_choice parameter here.

        Cash mechanics differ from options on purpose: entering a futures
        position commits MARGIN, not the full notional (no premium is
        paid or received) — cash only moves at exit, by the realized
        P&L. Daily unrealized P&L is still tracked and feeds the equity
        curve every bar (marking to market daily is real and correct);
        what's simplified is not literally moving cash day-by-day the way
        a real broker's daily MTM settlement would — the EQUITY CURVE
        this produces is the same either way, only the internal cash-vs-
        margin bookkeeping differs from a real daily-settlement
        statement, and that's a deliberate simplification, not an
        oversight.
        """
        underlying = get_underlying(underlying_symbol)
        indicator_df = ind.compute_all(
            underlying_bars, atr_period=self.equity_config.atr_period, rsi_period=self.equity_config.rsi_period,
            bb_period=self.equity_config.bb_period, sma_trend_period=self.equity_config.sma_trend_period,
        )
        n = len(indicator_df)
        if n < 2:
            raise ValueError("need at least 2 bars to run a futures backtest")

        cash = starting_cash
        margin_blocked = 0.0
        open_position: Optional[FOPosition] = None
        trades: List[FOTrade] = []

        first_date = indicator_df.index[0]
        equity_curve: List[FOEquityPoint] = [FOEquityPoint(
            date=first_date.date().isoformat(), cash=cash, margin_blocked=0.0,
            mark_to_market_value=0.0, equity=cash,
        )]

        for t in range(0, n - 1):
            next_idx = t + 1
            today = indicator_df.index[t].date()
            tomorrow = indicator_df.index[next_idx].date()
            tomorrow_open = float(indicator_df["open"].iloc[next_idx])
            tomorrow_close = float(indicator_df["close"].iloc[next_idx])

            if open_position is not None:
                contract: FutureContract = open_position.contract
                if contract.is_expired(tomorrow):
                    settle_price = fo_provider.price_future(contract, tomorrow, tomorrow_open).theoretical_price
                    pnl = self._close_futures_position(open_position, settle_price, tomorrow.isoformat(),
                                                       "EXPIRY_SETTLEMENT", trades)
                    cash += pnl
                    margin_blocked -= open_position.margin_blocked
                    open_position = None
                else:
                    quote = fo_provider.price_future(contract, tomorrow, tomorrow_open)
                    current_price = quote.theoretical_price
                    hit_stop = (
                        current_price <= open_position.stop_loss_price if open_position.side == "LONG"
                        else current_price >= open_position.stop_loss_price
                    )
                    hit_target = (
                        current_price >= open_position.take_profit_price if open_position.side == "LONG"
                        else current_price <= open_position.take_profit_price
                    )
                    if hit_stop or hit_target:
                        reason = "STOP_LOSS" if hit_stop else "TAKE_PROFIT"
                        pnl = self._close_futures_position(open_position, current_price, tomorrow.isoformat(),
                                                           reason, trades)
                        cash += pnl
                        margin_blocked -= open_position.margin_blocked
                        open_position = None

            if open_position is None:
                signal = self._signal_for_day(indicator_df, t)
                side = "LONG" if signal == SignalDirection.BUY else ("SHORT" if signal == SignalDirection.SELL else None)
                if side is not None:
                    today_close_spot = float(indicator_df["close"].iloc[t])
                    atr_at_decision = float(indicator_df["atr"].iloc[t])
                    expiry = self.expiry_resolver.next_monthly_expiry(underlying, today)
                    contract = FutureContract(underlying_symbol=underlying_symbol, expiry=expiry)
                    decision_price = fo_provider.price_future(contract, today, today_close_spot).theoretical_price

                    equity_now = equity_curve[-1].equity
                    decision = self.risk_manager.size_future(contract, side, decision_price, atr_at_decision,
                                                              equity_now, margin_blocked)
                    if decision.num_lots > 0:
                        fill_price = fo_provider.price_future(contract, tomorrow, tomorrow_open).theoretical_price
                        stop_distance = atr_at_decision * self.fo_config.futures_atr_stop_multiplier
                        target_distance = atr_at_decision * self.fo_config.futures_atr_target_multiplier
                        stop_price = fill_price - stop_distance if side == "LONG" else fill_price + stop_distance
                        target_price = fill_price + target_distance if side == "LONG" else fill_price - target_distance
                        margin_blocked += decision.margin_required
                        open_position = FOPosition(
                            contract=contract, side=side, num_lots=decision.num_lots, entry_price=fill_price,
                            entry_date=tomorrow.isoformat(), margin_blocked=decision.margin_required,
                            stop_loss_price=stop_price, take_profit_price=target_price,
                        )

            mtm_value = 0.0
            if open_position is not None:
                quote = fo_provider.price_future(open_position.contract, tomorrow, tomorrow_close)
                mtm_value = (quote.theoretical_price - open_position.entry_price) * open_position.signed_quantity
            equity = cash + mtm_value
            equity_curve.append(FOEquityPoint(
                date=tomorrow.isoformat(), cash=cash, margin_blocked=margin_blocked,
                mark_to_market_value=mtm_value, equity=equity,
            ))

        if open_position is not None:
            last_date = indicator_df.index[-1].date()
            last_spot = float(indicator_df["close"].iloc[-1])
            settle_price = fo_provider.price_future(open_position.contract, last_date, last_spot).theoretical_price
            pnl = self._close_futures_position(open_position, settle_price, last_date.isoformat(),
                                               "END_OF_BACKTEST", trades)
            cash += pnl
            equity_curve[-1] = FOEquityPoint(date=equity_curve[-1].date, cash=cash, margin_blocked=0.0,
                                             mark_to_market_value=0.0, equity=cash)

        ending_equity = equity_curve[-1].equity
        return FOBacktestReport(
            underlying_symbol=underlying_symbol, strategy_style=None, trades=trades, equity_curve=equity_curve,
            starting_equity=starting_cash, ending_equity=ending_equity,
            total_return_pct=((ending_equity / starting_cash) - 1.0) * 100.0 if starting_cash > 0 else 0.0,
        )

    @staticmethod
    def _translate_signal_to_option_action(signal: SignalDirection, style: OptionStrategyStyle):
        if signal == SignalDirection.BUY:
            if style == OptionStrategyStyle.DIRECTIONAL_LONG:
                return OptionType.CALL, "LONG"
            return OptionType.PUT, "SHORT"
        if signal == SignalDirection.SELL:
            if style == OptionStrategyStyle.DIRECTIONAL_LONG:
                return OptionType.PUT, "LONG"
            return OptionType.CALL, "SHORT"
        return None

    @staticmethod
    def _select_atm_strike(underlying_symbol: str, spot: float) -> float:
        from .fo_data_provider import STRIKE_INTERVALS
        interval = STRIKE_INTERVALS.get(underlying_symbol.upper(), 50)
        return round(spot / interval) * interval

    @staticmethod
    def _reanchor_stop(side: str, fill_price: float, decision) -> float:
        # Re-anchors the stop to the ACTUAL fill price using the same
        # PERCENTAGE the sizing decision used, not the raw stop_price
        # computed against yesterday's decision-time premium estimate —
        # the same "anchor risk management to the real fill, not the
        # estimate" discipline paper_trader.py already applies for equity.
        pct_to_stop = (decision.entry_price_estimate - decision.stop_price) / decision.entry_price_estimate \
            if side == "LONG" else (decision.stop_price - decision.entry_price_estimate) / decision.entry_price_estimate
        return fill_price * (1.0 - pct_to_stop) if side == "LONG" else fill_price * (1.0 + pct_to_stop)

    @staticmethod
    def _reanchor_target(side: str, fill_price: float, decision) -> float:
        pct_to_target = (decision.take_profit_price - decision.entry_price_estimate) / decision.entry_price_estimate \
            if side == "LONG" else (decision.entry_price_estimate - decision.take_profit_price) / decision.entry_price_estimate
        return fill_price * (1.0 + pct_to_target) if side == "LONG" else fill_price * (1.0 - pct_to_target)

    @staticmethod
    def _realized_cash_delta(position: FOPosition, exit_price: float) -> float:
        # LONG: bought at entry, sells at exit -> +exit*qty
        # SHORT: sold at entry (already credited), buys back at exit -> -exit*qty
        quantity = position.quantity
        return exit_price * quantity if position.side == "LONG" else -exit_price * quantity

    @staticmethod
    def _close_position(position: FOPosition, exit_price: float, exit_date: str, reason: str, trades: List[FOTrade]):
        quantity = position.quantity
        if position.side == "LONG":
            pnl = (exit_price - position.entry_price) * quantity
        else:
            pnl = (position.entry_price - exit_price) * quantity
        trades.append(FOTrade(
            contract=position.contract, side=position.side, num_lots=position.num_lots,
            entry_date=position.entry_date, exit_date=exit_date, entry_price=position.entry_price,
            exit_price=exit_price, exit_reason=reason, margin_required=position.margin_blocked,
            risk_amount=position.margin_blocked, pnl=pnl,
        ))

    @staticmethod
    def _close_futures_position(position: FOPosition, exit_price: float, exit_date: str, reason: str,
                                trades: List[FOTrade]) -> float:
        quantity = position.quantity
        pnl = (exit_price - position.entry_price) * quantity if position.side == "LONG" \
            else (position.entry_price - exit_price) * quantity
        trades.append(FOTrade(
            contract=position.contract, side=position.side, num_lots=position.num_lots,
            entry_date=position.entry_date, exit_date=exit_date, entry_price=position.entry_price,
            exit_price=exit_price, exit_reason=reason, margin_required=position.margin_blocked,
            risk_amount=position.margin_blocked, pnl=pnl,
        ))
        return pnl
