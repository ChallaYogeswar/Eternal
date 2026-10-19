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

## O0b — security base (built and verified)

Pure logic lives in `orchestration/`; everything that touches the real chain, a socket or a subprocess lives in the new
top-level `agent_chain/` package (the tripwire tests forbid `orchestration/` from importing phase packages, sockets or
subprocesses, and nothing in `orchestration/` or `eternal_core` may import `agent_chain`).

| Piece | Where | What it does |
|---|---|---|
| Capability tokens | `orchestration/capability.py` | Ed25519-signed, per run: agent, run, session, tools, max tier, call cap, expiry, origin channel. The verifier holds only the public key. A restarted verifier voids older tokens; `revoke_run` (also children) and `revoke_all` (kill switch). 13 denial reasons. |
| Identity envelope | `orchestration/identity.py` | `_identity` argument signed over identity, run, tool, args digest and a 30 s expiry, for MCP servers that must know the caller. |
| Per-run consent | `orchestration/consent.py` | WRITE calls pause until a human approves through typed text or an app tap; an agent or a spoken request cannot approve. One run, one tool, N uses, expiry. |
| Signed checkpoints | `orchestration/checkpoint.py` | Ledger head + count signed by a signer process that is not the runtime. Detects the "edit a row and recompute the chain" attack, truncation, forged signatures, gaps, staleness and off-box disagreement. |
| Crash-safe spend | `orchestration/model_policy.py` `SpendLedger` | Reservation ids with `open -> mark_sent -> settle/release`, per run and agent. A reaper releases never-sent reservations and keeps possibly-spent ones as `unknown`; late settles from abandoned threads are idempotent. Legacy `reserve/adjust` kept. |
| Chain wrapper | `agent_chain/gateway.py` | `CapabilityGateway` verifies the token, strips `_` arguments, adds the identity envelope for configured tools, then passes a PLAIN `WriteCallRequest` into the unchanged Phase 6/5/4 + RCAP chain. `kill()` revokes agent authority first, then runs the existing kill switch. |
| Per-run authority | `agent_chain/port.py` | Issues one token per run, takes the per-tool ROTATE grants from Phase 5's guarded `grant_tool_permission` (a quarantined session cannot start agent work), revokes everything on `close()`. Tier comes from the RCAP registry, never from the agent. |
| Signer socket | `agent_chain/signer_socket.py`, `scripts/orchestration/checkpoint_signerd.py` | One JSON line in, one out. Accepts only (head, count, prev); not a signing oracle. |
| Off-box copies | `agent_chain/sinks.py` | `CommandSink` runs an argv list (no shell) for scp over Tailscale or a git push. A failing sink is logged as `checkpoint_failed`, not silent. |

Tests: `tests/orchestration/test_capability.py`, `test_checkpoint.py`, `test_spend_reservations.py`, and
`tests/agent_chain/` (real QAP-P + RCAP + Phase 4/5/6 chain with only the MCP executor faked; mutation-checked: with
verification disabled, 10 of those tests fail).

### Running the signer as a separate OS user
```
sudo useradd --system --create-home eternal-signer
sudo -u eternal-signer python scripts/orchestration/checkpoint_signerd.py init --key ~/ckpt.key --pub ~/ckpt.pub
sudo -u eternal-signer python scripts/orchestration/checkpoint_signerd.py serve --key ~/ckpt.key \
     --state ~/state.json --socket /run/eternal-signer/signer.sock
```
Give the socket's group to the runtime user (mode 660). Copy only `ckpt.pub` where the Auditor can read it. The daemon
refuses to start if the key file is readable by group or other. Later the same `SignerPort` can be backed by a phone
WebAuthn approval instead.

### Honest limits of O0b
- Ed25519 tokens, not HMAC as the plan's wording said: the verifier can then hold a public key only.
- The OS-user separation is a deployment step; the tests prove the protocol and the refusal rules, not your OS setup.
- The capability layer adds "may this agent call this tool at all"; consent, voice gate, ROTATE and RCAP still decide
  independently (a WRITE tool is still refused without Phase 4 consent even with a valid token).
- Spend: a call whose request was sent and never answered stays counted as `unknown` (conservative), not refunded.

## Deliberately NOT in O0a (status after O0b noted above) (and why)
- ~~Capability tokens / verifier, per-run consent grants, signed external checkpoint~~ → built in O0b (above).
- **Wired to the real gateway in O0b** through `agent_chain/` (integration tests there). Still no agents (O1+) and no
  live MCP subprocess run with agent traffic.
- **Tool timeout cannot kill the MCP subprocess** (sync gateway; amendment A6) — the call is abandoned and reported.
- Token counts are estimates (len/4); providers do not report usage.
- Real-model behaviour (JSON reliability, injection resistance) is untested: the build sandbox cannot reach Ollama.
  The red-team harness and a real-model run on your machine are planned before any agent is trusted.
