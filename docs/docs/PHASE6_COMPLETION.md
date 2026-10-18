# Phase 6 Completion Report

## Merge
Phase 1-5 zip (both uploaded copies byte-identical) -> chat 1 -> chat 2, compared file by file after mapping chat 2's
`phase6/` layout onto `trading_risk_engine/`. Chat 1's tree already contained everything in chat 2 plus four bug fixes
(FOTrade.r_multiple, FO broker network-error handling, RCAP inspect_ring false positive, idempotent http_e2e). The only
chat-2-only artifact was `tests/test_fo_trading_engine.py` (12 tests) — ported to `tests/trading_risk_engine/`.

## Added in the completion pass
| Item | Where |
|---|---|
| F&O performance metrics in backtest results | `trading_risk_engine/instruments/fo_performance.py`, `FOTradingEngine`, `FOBacktestResult.performance` |
| F&O `get_order()` | `instruments/fo_kite_connect_broker.py` |
| F&O MARKET orders via `market_protection_pct` | broker, `FOTradingEngine`, both `fo_place_live_*` tools |
| 2025 NSE holidays | `instruments/calendar.py` |
| Go-live gate (master plan exit criterion) | `trading_risk_engine/go_live.py` |
| Tests | `tests/trading_risk_engine/test_phase6_completion.py` (25), MCP e2e +1 wire check |

## Validation (all run, fresh extraction of the final zip)
Full suite 669 passed (includes the 14 voice tests, with espeak-ng installed); narrative demo 38/38; live MCP stdio e2e
14/14; live HTTP daemon e2e 22/22.

## Cannot be closed from code (needs you / real environment)
1. Real Kite Connect calls on the static-IP host (paper first).
2. Real market data and weeks of real paper trading — `evaluate_go_live` needs that evidence; simulated data proves
   plumbing, not an edge.
3. Real hardware for Whisper / openWakeWord / ECAPA-TDNN / Bhashini voice stack.
4. Deferred by decision: stock F&O, commodities, SPAN margins.
