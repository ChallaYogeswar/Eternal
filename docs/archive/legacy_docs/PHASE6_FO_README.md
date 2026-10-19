# Phase 6 Extended — F&O (Index Options + Index Futures)

(Merged into the consolidated Eternal project. Package names: `trading_risk_engine` = old `phase6`,
`device_control_gateway` = old `phase5`, `voice_consent_gateway` = old `phase4`.)


A second workstream on top of the equity engine above: index options and
index futures, scoped to **NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX**
only (stock F&O and commodities are explicitly out of scope for this round).
Composition, not modification — every F&O class is new and sits beside the
equity classes above; nothing in this section required editing an existing
equity file except two lines in `trading_risk_engine/gateway.py` (see below).

| Build plan bullet (F&O extension) | File |
|---|---|
| Black-Scholes-Merton pricing + Greeks | `trading_risk_engine/options_pricing.py` |
| Contract lot-size registry (NSE, Nov 2025 circular) | `trading_risk_engine/instruments/underlyings.py` |
| Weekly/monthly expiry resolution (NSE 2026 holiday circular) | `trading_risk_engine/instruments/calendar.py` |
| Option/future contract models | `trading_risk_engine/instruments/contracts.py` |
| Margin approximation (explicitly NOT real SPAN) | `trading_risk_engine/instruments/margin.py` |
| F&O risk config | `trading_risk_engine/instruments/fo_config.py` |
| Synthetic option-chain data provider | `trading_risk_engine/instruments/fo_data_provider.py` |
| F&O position sizing (ATR/margin-capped) | `trading_risk_engine/instruments/fo_risk.py` |
| Options + futures backtester | `trading_risk_engine/instruments/fo_backtester.py` |
| F&O paper broker | `trading_risk_engine/instruments/fo_paper_broker.py` |
| F&O Kite Connect broker | `trading_risk_engine/instruments/fo_kite_connect_broker.py` |
| MCP tool layer (11 new tools) | `mcp_servers/trading_engine/data.py`, `server.py` |
| Tool tiers + live-order gating | `trading_risk_engine/registry_ext.py`, `trading_risk_engine/gateway.py` |

### Design: reuse, don't rebuild

The signal itself — BUY/SELL/HOLD from `trading_risk_engine/signals.py` — doesn't know or
care whether the price series it's given belongs to a stock or an index. So
`FOTradingEngine` (in `data.py`) doesn't build a second signal engine: it
reuses the **exact same `SignalEngine` instance** the equity engine already
built, running it against the index's own simulated price path. F&O gets its
own config/risk/backtester/broker classes because those genuinely differ
(lots, not shares; margin, not just cash) — but the directional opinion is
one engine, one set of rules, for both stocks and indices.

`FOTradingEngine` also reuses the equity engine's `Ledger`, `Config.data_dir`
(the F&O paper broker's SQLite file lives alongside the equity one), and its
`DailyReauthManager` (one Kite Connect login backs both equity and F&O live
orders for one broker account — there is no second login flow).

### The 11 new MCP tools

| Tool | Tier | What it does |
|---|---|---|
| `fo_get_option_chain` | READ_ONLY | Full CE+PE chain around the money, Black-Scholes-priced against a simulated spot |
| `fo_get_signal` | READ_ONLY | The equity engine's own signal, run on the index |
| `fo_run_options_backtest` | READ_ONLY | No-lookahead backtest, `DIRECTIONAL_LONG` or `INCOME_SHORT` |
| `fo_run_futures_backtest` | READ_ONLY | No-lookahead backtest, monthly cycle only |
| `fo_get_paper_positions` | READ_ONLY | Open F&O paper positions |
| `fo_get_paper_margins` | READ_ONLY | Paper cash/margin balances |
| `fo_place_paper_option_order` | WRITE | Manual-override paper order (explicit strike/side/lots) |
| `fo_place_paper_future_order` | WRITE | Manual-override paper order, next monthly expiry |
| `fo_close_paper_position` | WRITE | Marks a position to today's theoretical price, realizes P&L |
| `fo_place_live_option_order` | SENSITIVE | Real order via `FOKiteConnectBroker` |
| `fo_place_live_future_order` | SENSITIVE | Real order via `FOKiteConnectBroker` |

The two live-order tools are gated by the exact same chain
`trading_place_live_order` already had — active non-quarantined session, top
trust tier, non-voice confirmation, fixed-IP host role, valid daily Kite
login, order rate limit — with **zero new gateway logic**. The only change
`trading_risk_engine/gateway.py` needed was turning its single hardcoded
`LIVE_ORDER_TOOL_NAME` into a set, `LIVE_ORDER_TOOL_NAMES`, and checking
membership instead of equality. A backward-compatible `LIVE_ORDER_TOOL_NAME`
alias is kept so the pre-existing equity gateway test didn't need touching.

### Validated — in the merged Eternal project, from a fresh extraction

All four layers were actually executed (not just compiled):

| Layer | Result |
|---|---|
| Unit tests, `tests/trading_risk_engine/` (equity + 9 F&O files + F&O data-layer file) | 293 pass |
| Full project suite (`tests/`) | 618 pass; 14 fail, all in `tests/voice_consent_gateway` (TTS / STT / wake-word / speaker-verify) because the eSpeak system library is not installed in the build sandbox. Install `espeak-ng` on the target machine and re-run. |
| Narrative demo `scripts/trading_risk_engine/demo_exit_criteria.py` (incl. new section 8b, F&O) | 38/38 checks |
| Live MCP stdio subprocess e2e `scripts/trading_risk_engine/mcp_e2e.py` (incl. F&O + cross-subprocess persistence + kill switch) | 13/13 |
| Live HTTP daemon e2e `scripts/trading_risk_engine/http_e2e.py` (incl. F&O) | 22/22, also on a second run against persisted state |

Running the integration layer for real surfaced four genuine defects that unit tests had not caught
(all fixed, each with a regression test):

1. `FOTrade.r_multiple` referenced a corrupted attribute (`phase6_eternal_phase6_risk_amount`), so every
   F&O backtest that produced at least one trade crashed on serialization. A project-wide cleanup of the
   `phaseN_eternal_phaseN_*` naming corruption was applied (identifiers, docstrings, comments).
2. `FOKiteConnectBroker.place_option_order` / `place_future_order` let a network failure while fetching
   the instrument dump escape as a raw exception; a live-order call now always degrades to a REJECTED order.
3. `rcap_p/inspect_ring.py`: the punctuation-stripped "compact" scan view fed the 80+ character
   base64-blob heuristic, so any ordinary sentence of 80+ characters was flagged as obfuscation and held
   for approval (this blocked `trading_check_recovery_math`'s note field). The blob heuristic now runs on
   the spaced view only; real base64 blobs are still flagged (tests added both ways).
4. `scripts/.../http_e2e.py` asserted an exact paper position quantity and so failed on a second run
   against persisted state; it now asserts `>=`.

### A note on futures backtests showing 0 trades at INR 10L

Independently re-verified in the merged project: at the default INR 10,00,000 starting capital the
futures backtest returns **0 trades for every one of the five underlyings** (not only BANKNIFTY). One
lot's ATR-based stop distance (1.5 x ATR x lot size) risks more than the 1% of equity that `FOConfig`
allows, so `FORiskManager.size_future` correctly sizes to zero lots. At INR 50,00,000 the same backtests
trade (NIFTY 4, BANKNIFTY 5, FINNIFTY 1, MIDCPNIFTY 4, SENSEX 9 in the simulated 250-day window).
Options backtests trade at INR 10L (NIFTY 17, SENSEX 12). This is intended risk-model behaviour.

### Known simplification carried over from the equity engine

`calendar.py` only has 2026 NSE holidays loaded. Any F&O backtest whose date
range extends into 2025 or earlier raises a clear `ValueError` rather than
silently using wrong holiday data — fail-closed by design, not a bug. Keep
backtest lookbacks inside 2026 (roughly ≤270 days back from "today" in this
project's current timeline) until a later phase loads more years.

### Known simplifications of the F&O live broker (`FOKiteConnectBroker`)

- `get_order()` is implemented (Kite `order_history`, latest entry mapped to FILLED/REJECTED/CANCELLED/PENDING;
  returns `None` on unknown order or any lookup error). Covered by fake-client tests only — never run against real Kite.
- F&O live-order tools accept `market_protection_pct`. A MARKET order without a positive value is refused at the
  gateway AND again inside the broker (never sent). LIMIT orders are still the recommended choice.
- Margin figures are approximations, not exchange SPAN.
- Prices, option chains and underlying paths are simulated; Kite network calls (instrument dump,
  order placement) cannot be exercised from the build sandbox. They are covered by fake-client tests.
- Calendar covers 2025 and 2026 and fails closed elsewhere. 2025 comes from one source (NSE circular NSE/COM/65590, the
  commodity-segment circular; only equity-relevant full-day closures were kept) — re-verify against the equity circular
  before trusting pre-2026 expiry dates for anything beyond backtesting. 2027 can be added once NSE publishes it (~Dec 2026).
