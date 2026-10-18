"""
Phase 6 Configuration
----------------------
Same pattern as qap_p/config.py (Phase 1), rcap_p/config.py (Phase 3),
voice_consent_gateway/config.py (Phase 4), and device_control_gateway/config.py (Phase 5): every tunable
documented against the specific Eternal_Master_Build_Plan.md line it
implements.
"""
import os
from dataclasses import dataclass, field
from typing import Tuple


@dataclass
class Config:
    # --- Data / audit ---
    data_dir: str = os.environ.get("PHASE6_DATA_DIR", "./data")
    ledger_db_path: str = ""
    paper_broker_db_path: str = ""
    daily_reauth_db_path: str = ""
    ip_history_db_path: str = ""

    # --- Risk management (build plan Section 7.2: "The math correction")
    # "fixed-fractional position sizing (1-2% risk/trade, ATR-based)" ---
    risk_per_trade_pct: float = float(os.environ.get("PHASE6_RISK_PER_TRADE_PCT", 0.01))
    max_risk_per_trade_pct: float = 0.02  # hard ceiling risk.py refuses to exceed regardless of config above
    atr_period: int = 14
    atr_stop_multiplier: float = 1.5   # stop_loss = entry - 1.5*ATR
    atr_target_multiplier: float = 3.0  # take_profit = entry + 3.0*ATR -> 2:1 reward:risk by default
    # Beyond the build plan's literal wording: a professional-practice
    # addition, not a spec requirement. Without a cap, a stock with a very
    # small ATR relative to price (a quiet, low-volatility stock) can
    # produce an oversized share count from the risk formula alone —
    # capping the dollar value of any single position is standard
    # portfolio-construction hygiene layered on top of the spec's own
    # risk-per-trade rule, not a replacement for it.
    max_position_pct_of_equity: float = 0.20
    max_total_exposure_pct_of_equity: float = 1.0  # cash account, no margin, by default
    max_concurrent_positions: int = 10

    # --- Max-drawdown circuit breaker (build plan Section 7.2: "a
    # max-drawdown circuit breaker that stops trading for the period
    # rather than pushing harder") ---
    max_drawdown_circuit_breaker_pct: float = float(os.environ.get("PHASE6_MAX_DD_BREAKER_PCT", 0.10))
    circuit_breaker_period: str = "month"  # "week" | "month" — the unit "stops trading for the period" resets on

    # --- Backtesting costs — ignoring these is the single most common way
    # an amateur backtest overstates its own edge ---
    commission_pct: float = float(os.environ.get("PHASE6_COMMISSION_PCT", 0.0003))  # 3 bps/side, adjust to your broker's real schedule
    commission_flat: float = float(os.environ.get("PHASE6_COMMISSION_FLAT", 20.0))  # INR flat per order, Zerodha-style ballpark
    slippage_bps: float = float(os.environ.get("PHASE6_SLIPPAGE_BPS", 5.0))  # 5 bps adverse slippage per fill

    # --- Performance metric conventions ---
    trading_days_per_year: int = 252
    annual_risk_free_rate_pct: float = float(os.environ.get("PHASE6_RISK_FREE_RATE_PCT", 0.065))  # ~ Indian T-bill ballpark; override at deployment

    # --- SEBI / Kite Connect regulatory gates (build plan Section 7.2,
    # "Regulatory landscape") ---
    # "IP changes are limited to one per calendar week."
    max_ip_changes_per_week: int = 1
    # "10 orders/second is the hard rate-limit ceiling per client account."
    # Deliberately configured well under the hard ceiling, not AT it —
    # see regulatory.py's module docstring for why headroom matters here.
    max_orders_per_second: float = float(os.environ.get("PHASE6_MAX_ORDERS_PER_SEC", 3.0))
    # Kite Connect access tokens are documented (Kite Connect FAQ / forum,
    # not a fixed published SLA) as purged sometime between roughly 05:00
    # and 07:30 IST daily. This default is a conservative placeholder —
    # CONFIRM the current behavior in Kite Connect's own docs at
    # deployment time rather than trusting this number indefinitely.
    daily_token_cutover_hour_ist: int = int(os.environ.get("PHASE6_TOKEN_CUTOVER_HOUR_IST", 7))
    daily_token_cutover_minute_ist: int = int(os.environ.get("PHASE6_TOKEN_CUTOVER_MINUTE_IST", 30))

    # "This means: monitoring and position checks stay reachable from the
    # phone anywhere; only the actual buy/sell call must originate from
    # the fixed IP." — order placement is refused unless the process
    # reports itself as running with this role. There is no way to
    # verify a real public IP from this sandbox (network egress here is
    # restricted to package registries — see regulatory.py's module
    # docstring); this is the documented seam.
    trading_host_role: str = os.environ.get("PHASE6_TRADING_HOST_ROLE", "")
    required_trading_host_role: str = "home-server-fixed-ip"

    # Kite Connect FAQ: an unprotected (market_protection=0) market order
    # is rejected outright, and market orders carry meaningfully more
    # slippage risk than limit orders in general. Market orders are
    # therefore opt-in, off by default — the build plan's own trading
    # section never actually calls for market orders specifically, and
    # "prefer limit orders" is the safer default for something this
    # sensitive.
    allow_market_orders: bool = os.environ.get("PHASE6_ALLOW_MARKET_ORDERS", "0") == "1"
    default_market_protection_pct: float = 3.0

    # --- Live trading master switch (build plan Section 8, Phase 6:
    # "Sub-phase B ... only after A is proven and Phase 5 has run in
    # daily use") --- Defaults OFF. This is a real-world usage bar the
    # build plan itself sets, not something code can verify — this flag
    # is the concrete stand-in: it must be deliberately, separately
    # turned on, it is never on by default, and turning it on does not
    # bypass any of the other gates (top-trust-tier, non-voice
    # confirmation, static-IP host role, daily reauth, rate limit) below.
    live_trading_enabled: bool = os.environ.get("PHASE6_LIVE_TRADING_ENABLED", "0") == "1"

    # --- Top trust tier (reuses Phase 5's exact gate/threshold pattern —
    # qap_p/trust.py's gates_sensitive_tier docstring names "trade
    # execution" explicitly, written in Phase 1 before Phase 5 or 6
    # existed) ---
    top_trust_tier_min_score: float = float(os.environ.get("PHASE6_TOP_TRUST_MIN_SCORE", 0.85))

    # --- Universe (build plan Section 7.2: "yfinance (NSE .NS tickers)") ---
    default_universe: Tuple[str, ...] = field(default_factory=lambda: (
        "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    ))

    # --- Signal engine (build plan Section 7.2: "Signals as explicit,
    # auditable rules, not a black box") ---
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    bb_period: int = 20
    bb_num_std: float = 2.0
    sma_trend_period: int = 50
    macd_fast_period: int = 12
    macd_slow_period: int = 26
    macd_signal_period: int = 9
    stoch_k_period: int = 14
    stoch_d_period: int = 3
    stoch_oversold: float = 20.0
    stoch_overbought: float = 80.0
    # The combined score (weighted average across active rules, range
    # [-1, +1]) must clear this bar to become an actionable BUY/SELL —
    # asymmetric-looking but actually symmetric (sell_threshold is
    # negative), kept as two named fields rather than one +/- magnitude
    # so a person can deliberately make the strategy more reluctant to
    # sell than to buy (or vice versa) without touching rule code.
    signal_buy_threshold: float = 0.35
    signal_sell_threshold: float = -0.35

    def __post_init__(self):
        os.makedirs(self.data_dir, exist_ok=True)
        self.ledger_db_path = self.ledger_db_path or os.path.join(self.data_dir, "phase6_ledger.db")
        self.paper_broker_db_path = self.paper_broker_db_path or os.path.join(self.data_dir, "paper_broker.db")
        self.daily_reauth_db_path = self.daily_reauth_db_path or os.path.join(self.data_dir, "daily_reauth.db")
        self.ip_history_db_path = self.ip_history_db_path or os.path.join(self.data_dir, "ip_history.db")
        if self.risk_per_trade_pct > self.max_risk_per_trade_pct:
            raise ValueError(
                f"risk_per_trade_pct ({self.risk_per_trade_pct}) exceeds the hard ceiling "
                f"({self.max_risk_per_trade_pct}) — Eternal_Master_Build_Plan.md Section 7.2 names "
                "1-2% risk/trade explicitly; this is not a per-deployment style choice."
            )
