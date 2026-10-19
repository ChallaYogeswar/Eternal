# Orchestration layer — status

Design: `docs/ORCHESTRATOR_LAYERS_PLAN.md` (v2 by owner + v2.1 amendments in section 14).
Package: `orchestration/` — composes `eternal_core`; nothing in `eternal_core` or any earlier phase was modified.

## O0a — built and verified (this drop)

| Module | Role |
|---|---|
| `contracts.py` | AgentSpec (frozen, `spec_hash`), Budget, Labels, Task, Finding, StepRecord, AgentResult, ToolInfo |
| `labels.py` | untrusted-observation wrapper (delimiter-safe), web-vs-internal join guard, sensitivity ceiling |
| `evidence.py` | per-run evidence registry (broker-write, read-only view), finding reconciliation (fabricated refs dropped; model cannot lower a check's severity) |
| `broker.py` | `ToolBroker` — sole gateway holder; allowlist, tier, repeat/denial/call caps, reserved-arg rejection, consent port (fail-closed), pre-dispatch kill re-check, fixed identity stamping, decoy handling, timeouts; `BrokerFactory` |
| `runtime.py` | `AgentRunner` — strict one-JSON-action loop, repair retry, budgets, kill checks, label joins, never raises |
| `model_policy.py` | `PolicyRouter` — cloud only if spec allows AND labels allow (not secret / not PII), no silent fall-through, `SpendLedger` atomic reserve-before-call |
| `ledger.py` | single-writer hash-chained run ledger (wraps `eternal_core.audit.Ledger`), `head()` for O0b's checkpoint |

Verified: 74 tests in `tests/orchestration/` (scripted fake model + fake gateway), including AST tripwires
(no gateway/MCP/network/dynamic-import use in `orchestration/`; only `broker.py` references a gateway; `eternal_core`
never imports `orchestration`) and drift guards against the real `CallerKind` / `ConfirmationChannel` values.
Mutation-checked: breaking each key guard (per-run source label, pre-dispatch kill re-check, reserved args,
caller kind, evidence resolution, web/internal guard, PII-to-cloud, token budget, trailing-JSON parse) makes a test fail.

## P1 + P2 — providers and validation harness (this drop)

- `orchestration/providers/`: Gemini (Interactions API, stateless, `store:false`) and Groq/OpenAI-compatible providers
  with injectable HTTP, 401/403/429/5xx mapping, retry-after, provider-reported token usage; env-based factory
  (free tier = PUBLIC-only ceiling).
- `PolicyRouter` upgrade: `CloudEntry` (per-provider data ceiling, prices, rpm/rpd/tpm/tpd limits), sliding-window
  `RateLimiter`, worst-case spend reservation settled with real usage, `blocked_by_policy` reason.
- `orchestration/validation/`: corpus, 15 cases, code scoring, simulated good/sloppy/vulnerable models, metering and
  pacing, Markdown + JSON report, CLI. See `docs/VALIDATION_HARNESS.md`.
- Not verified here: live Gemini/Groq calls (sandbox has no route). Run `--smoke` then `--run` on your machine.

## Deliberately NOT in O0a (and why)
- **Capability tokens / verifier, per-run consent grants, signed external checkpoint → O0b.** Until O0b the broker's
  allowlist is the only per-agent enforcement and WRITE tools always pause (`awaiting_confirmation`). Tripwire tests
  are not proof; the verifier is the real control.
- **Not wired to the real gateway yet.** Tests use a fake gateway with the same call signature as `Phase6CoreGateway`.
  No agents exist yet (O1+), so there is no wire-level (MCP/HTTP) layer to validate for O0a.
- **Tool timeout cannot kill the MCP subprocess** (sync gateway; amendment A6) — the call is abandoned and reported.
- Token counts are estimates (len/4); providers do not report usage.
- Real-model behaviour (JSON reliability, injection resistance) is untested: the build sandbox cannot reach Ollama.
  The red-team harness and a real-model run on your machine are planned before any agent is trusted.
