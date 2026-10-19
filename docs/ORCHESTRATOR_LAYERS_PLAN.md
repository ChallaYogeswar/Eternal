# Eternal — Orchestrator Layers Plan v2.1 (v2 by owner + amendments in section 14)

Status: PLAN ONLY, ready for O0. Supersedes v1 (2026-10-09). No code written.
Scope (unchanged): LLM sub-agents; first set = Auditor, Reviewer, Researcher, Writer, Task Issuer;
every tool call goes through the existing gateway chain.

Section 11 holds decisions with defaults. Anything marked DEFAULT is what the plan assumes unless
you override it before coding.

---

## 0. Changes from v1 (why this is v2)

| # | v1 flaw | v2 fix |
|---|---|---|
| 1 | "L0 unchanged" but gateway was to re-enforce per-agent allowlists (it has no agent identity) | One additive L0 component: delegated capability tokens + verifier (s6.1). Existing phase gateways untouched |
| 2 | Stored tasks "inert" yet dispatchable -> second-order injection | Provenance labels; tainted tasks need human approval; no autonomous dispatch in v1 (s6.3) |
| 3 | RAG + web in one run, cloud supervisor sees local-only findings -> exfiltration | Data classification separate from tool tier; cloud eligibility = min over context, enforced in router (s6.4) |
| 4 | Evidence checked for shape only; model can fabricate | Per-run evidence registry; refs must resolve; code-generated pass/fail (s3, s4.1) |
| 5 | Hash chain self-verifiable, Auditor not independent | External signed checkpoint; Auditor reads stores directly (s6.9) |
| 6 | Unattended consent undefined | Per-run scoped consent grants + `awaiting_confirmation` state (s6.5) |
| 7-14 | RCAP decoys/CONTAIN, voice origin, task closure, kill TOCTOU, budgets, workspace allowlist, concurrency, handoff validation | Sections 3, 6, 7, 9 |

---

## 1. What exists today (unchanged facts)

- `eternal_core/graph`: linear LangGraph (retrieve -> optional tools -> try_local -> try_cloud ->
  retrieval_fallback -> finish). One LLM call per turn; no planner/loop; tool_call is caller-supplied.
- `LLMProvider.generate(system, messages)` is text-in/text-out, no `tools` param;
  `tests/eternal_core/test_zero_write_access.py` enforces it.
- Tool chain: Phase6Gateway -> Phase5Gateway (kill switch, top-trust tier) -> Phase4Gateway (voice
  gate, consent, explanation log) -> RCAPGateway (PERIMETER, ROTATE, INSPECT, DECEIVE, CONTAIN,
  ATTRIBUTE, FASTLANE). `Phase6CoreGateway` is the graph's adapter.
- `CallerKind.AUTOMATED_ROUTINE` is never FASTLANE-eligible: sub-agents use it.
- No task/reminder system and no file-reading tool over MCP exist.

Design consequences:
1. New package `orchestration/` composes `eternal_core`; `eternal_core` is not modified.
2. Agents use a plan-act-observe JSON protocol over plain `generate()`; a broker executes tools.
3. Tool access reuses the existing chain, plus per-run scoping via capability tokens (s6.1).

## 2. Layer model

```
L5  Interfaces        HTTP API, voice intent, (future) phone app
L4  Supervisor        classify -> plan -> validate -> dispatch -> collect -> compose
L3  Specialists       Auditor | Reviewer | Researcher | Writer | Task Issuer
L2  Agent runtime     AgentSpec, run loop, ToolBroker, budgets, model router, labels,
                      evidence registry, consent grants, run ledger
L1  Capability layer  MCP servers (existing + 4 new, s7)
L0  Security          QAP-P, RCAP-P, consent, voice gate, trust tiers, kill switch
                      + CapabilityVerifier (additive, s6.1)
```

Rules:
- A layer calls only the layer directly below it.
- Only `broker.py` holds a gateway reference. Specialists never call each other.
- Supervisor holds no tools.
- Agents never hold the human session token. They hold only a run-scoped capability token.

## 3. Core contracts (L2)

All frozen/serializable, flat-state style like `EternalState`.

**Labels (new, applied to every datum entering a run's context)**
- `provenance`: `user_typed | user_voice | rag_internal | web | tool_output | agent_output`
- `sensitivity`: `public | internal | secret`
- Rule: a datum's labels never decrease. Context labels = join (max sensitivity, union of provenance).
  Untrusted provenance = `web | tool_output | agent_output | rag_internal` (rag docs can contain
  injected text too).

**AgentSpec (frozen, version-controlled code)**
`name`, `purpose`, `system_prompt`, `allowed_tools`, `max_tool_tier`, `max_data_sensitivity`
(highest sensitivity the agent may hold in context), `output_schema`, `budget`, `model_policy`,
`can_write`, `spec_hash` (computed, written to every ledger entry).

**Budget**: `max_steps`, `max_tool_calls`, `max_tokens`, `wall_clock_s`, `per_call_timeout_s`
(model and tool), `max_denials` (default 3), `max_repeat_calls` (default 2, identical tool+args).

**CapabilityToken (L0 verifier, s6.1)**
`run_id`, `agent`, `tools[]`, `max_tier`, `caller_kind=AUTOMATED_ROUTINE`, `origin_channel`,
`call_cap`, `exp`, `parent_session_id`, `nonce`, HMAC signature.

**Task**: `task_id`, `run_id`, `goal`, `inputs` (data, labelled), `constraints`, `parent_task_id`,
`depth`, `origin_channel` (typed | voice | api), `owner_kind` (agent:<name> | human),
`required_tools[]` (structured, never inferred from free text).

**StepRecord**: model JSON action, claimed_reason (logged as unverified), tool/args, broker verdict,
gateway verdict, `decoy` flag, observation (truncated), labels, tokens, latency, spec_hash.

**EvidenceRegistry (per run, broker-owned)**: every tool observation gets an `evidence_id` +
content hash + source (file path+line range / URL / ledger hash). Only broker writes it.

**Finding**: `severity`, `category`, `claim`, `evidence_refs[]` (must be evidence_ids that exist in
this run's registry), `suggested_action`, `origin` (`check` | `model`). `origin=check` findings
carry check-assigned severity that the model cannot lower. Unresolvable refs -> finding dropped and
counted in `AgentResult.rejected_findings`.

**AgentResult**: `status` (ok | refused | awaiting_confirmation | budget_exhausted | denied_out |
failed | killed), `output` (schema-validated), `steps`, `evidence_ids`, `degraded`,
`rejected_findings`, `labels`.

**ConsentGrant (s6.5)**: `run_id`, `tool`, `max_calls`, `exp`, `approved_via` (non-voice channel).

**Run loop**: check session live -> build prompt (untrusted wrapper around labelled observations) ->
`generate()` with timeout -> re-check session live -> strict-parse one JSON action
(`tool_request` | `final_answer`; first top-level object only, reject trailing text) -> validate
action (allowlist, budget, repeat-detector, args against tool input schema + size caps) ->
broker executes -> observation labelled and stored -> repeat. One repair retry on malformed JSON,
then `failed`. Every step appended to run ledger.

**ToolBroker**: sole gateway holder. Per call: allowlist + tier check, token verify (local),
stamp `AUTOMATED_ROUTINE` + `source_label=agent:<name>:<run_id>`, record claimed_reason, call gateway,
mark decoy responses, register evidence, apply labels, count denials. A gateway denial is an
observation; the broker never retries around it. Kill-state re-check immediately before dispatch;
in-flight MCP subprocess is cancellable.

**Model router**: reuse `LLMRouter`. `model_policy` is enforced INSIDE the router per request using
current context labels (s6.4); a local failure never silently falls through to cloud when policy or
labels forbid it.

## 4. Specialists (L3)

### 4.1 Auditor
- Verifies; read-only; local-only (`max_data_sensitivity=secret`, cloud forbidden).
- Deterministic checks decide pass/fail (ledger chain verify per phase, kill-switch state,
  consent/vault/session snapshot, go-live evaluation, recent denials, run-ledger chain and
  checkpoint match). The pass/fail table is generated by code; LLM writes prose only and cannot
  lower a check's severity or flip a result.
- `audit_reader` reads stores directly and verifies against the external checkpoint (s6.9).
- Runs exclusively (no concurrent runs) so snapshots are consistent.
- Output: `Finding[]` (origin=check preferred) + table.

### 4.2 Reviewer
- Reviews diffs/docs/Writer drafts vs conventions. Local-only (DEFAULT).
- Tools: `workspace_list`, `workspace_read_file` (allowlist-based, s7), RAG.
- Verdict: approve | changes_requested. Findings need file:line refs resolving to files actually
  read this run.
- Separation: never reviews its own run's Writer output in a shared context; fresh context, and uses a
  different model tier than the Writer when available (log when it can't).
- Test execution deferred (needs sandbox).
- Repo comments/docs/code strings are untrusted input.

### 4.3 Researcher
- Tools: `web_search`, RAG — but NEVER both in the same run (s6.4). Two modes:
  - `mode=web`: web only; context may not contain internal/secret data; query text is scanned for
    canary tokens and for content copied from internal sources before dispatch.
  - `mode=docs`: RAG only; local-or-cloud per labels.
- Output: `claims[]` `{statement, source_urls[], confidence, retrieved_at}`; each source URL must be
  in the evidence registry (actually returned by search/fetch this run); else labelled "unsourced".
- Final answer ends with Sources.

### 4.4 Writer
- Tools: RAG (read), `drafts_save` (only write). No send/post/publish anywhere in v1.
- Profile/PII passed as task input is labelled `internal`; if the run's model tier is cloud, that is
  PII egress and is flagged in the ledger (and gated by decision 11.5).
- Writer -> Reviewer loop: max 2 rounds, orchestrated only by Supervisor.

### 4.5 Task Issuer
- Converts goals into persisted tasks and human reminders (DEFAULT: both). It issues and tracks; it
  never executes work and there is NO autonomous dispatch in v1 — dispatch happens only on explicit
  human request or approval.
- Tasks carry structured `owner_kind`, `required_tools[]`, `due`, `depends_on[]`, `status`,
  `provenance`/`labels`, `origin_channel`.
- Tainted-task rule: a task whose content derives from untrusted provenance is created with
  `needs_human_approval=true`; never dispatchable until a human approves it.
- Closure rule (enforced server-side in the `tasks` MCP server, not just in the broker):
  human-owned tasks are closed only by a call carrying a human-authenticated identity; agent-owned
  tasks only by the Supervisor on a validated `AgentResult`. The Auditor has no write tool; "verified
  by Auditor" means Supervisor records an Auditor result as the closure evidence.
- Guards: per-run creation quota, cycle detection on dependencies, tasks needing tools above the
  target agent's ceiling are created as `owner_kind=human`.

## 5. Supervisor (L4)

- Classifies intent from the RAW user message only (never from tool/specialist output): rules first
  for obvious intents, LLM routing only if no rule matches.
- Plan validation in code: `allowed_tools_subset` and `budget_slice` are computed by code as subsets
  of the target spec, never taken from the LLM's plan. Plan shape caps: max depth 2, max total tasks,
  global budget split into slices.
- Dispatch sequential by default; parallel only for independent READ_ONLY tasks, never with the
  Auditor.
- Compose step: specialist outputs enter as labelled data fields. The compose model is chosen by the
  min-cloud-eligibility over all inputs (local-only Auditor/Reviewer findings force a local compose or
  a redacted summary). Holds NO tools.
- Fail-soft: no model reachable -> degraded retrieval-only answer, stated as such.
- Origin propagation: `origin_channel` from the request is copied into every Task and token.

## 6. Security design

1. **Delegated capability tokens (the one L0 addition).** At run start the Supervisor requests a
   token from a `CapabilityIssuer` bound to the live session; it is attenuated (tools = spec
   allowlist ∩ plan subset, tier ≤ spec ceiling, call_cap, short TTL, run_id). A new
   `CapabilityVerifier` sits at the Phase6 entry: unsigned/expired/out-of-scope/revoked -> deny before
   the chain. Signing key lives in the gateway process, unreadable by agents/broker code. Tokens are
   revoked on run end and on kill. Phase5/4/RCAP are unmodified and still make the real decision;
   the verifier only adds "is this agent+run allowed this tool at all". Broker allowlist check stays
   as defense in depth, not the only enforcer.
2. **Identity & ceilings.** All agent calls `AUTOMATED_ROUTINE`, never FASTLANE. Tool tables:

   | Agent | READ_ONLY | WRITE | SENSITIVE |
   |---|---|---|---|
   | Auditor | audit tools | none | none |
   | Researcher | web_search xor RAG | none | none |
   | Reviewer | workspace_*, RAG | none | none |
   | Writer | RAG | drafts_save | none |
   | Task Issuer | tasks_list | tasks_create, tasks_update | none |
   | Supervisor | none | none | none |

   Trading, device control, kill switch, home automation: excluded from every agent in v1.
3. **Prompt injection & second-order injection.** Untrusted wrapper on all observations; agent
   outputs are schema-validated data fields, never concatenated into another agent's system prompt;
   plan structure comes only from the Supervisor; RCAP INSPECT still scans responses. Provenance
   labels propagate into stored tasks/drafts; untrusted-derived items cannot be auto-actioned.
4. **Exfiltration / egress.** Classification (`public|internal|secret`) is separate from tool tier.
   Cloud eligibility = min over everything in context; enforced in the router per call; spec
   `model_policy` is an upper bound only. Never RAG+web in one run. Outbound web queries scanned for
   canaries and internal-content echo. Defaults: Auditor, Reviewer local-only; Researcher(web) and
   Writer may use cloud only with context ≤ `internal` per decision 11.5. Every cloud call flagged
   `external_request`.
5. **Consent for unattended runs.** Per-run `ConsentGrant`s (tool, max_calls, TTL) approved at run
   start via the non-voice confirmation channel. Without a grant a WRITE call yields
   `awaiting_confirmation` (run pauses, not fails, not blanket-approved). Grants expire with the run.
6. **Voice origin.** `origin_channel` rides in Task and token; for `user_voice` origin the voice gate
   applies to every downstream WRITE; no laundering a voice request into an unguarded agent action.
7. **Kill switch (TOCTOU).** Check before generate, after generate, and immediately before tool
   dispatch; cancel in-flight MCP subprocess; parallel tasks all observe the same state; runs refuse to
   start while killed; tokens revoked on kill. Result `killed`.
8. **RCAP interaction.** DECEIVE responses are flagged `decoy=true` by the broker and are never
   evidence nor cited in findings. CONTAIN risk from retry loops: `max_denials` ends the run as
   `denied_out`; repeat-detector blocks identical calls; denials attributed to the agent
   `source_label`, never silently to the human session.
9. **Run ledger integrity.** Hash chain plus an external signed checkpoint (head hash + count,
   signed with a key not readable by agents/runtime, and/or copied off-box) written at run end and
   periodically. Auditor verifies chain AND checkpoint; a recomputed full chain (double tamper) is
   caught by checkpoint mismatch. Run ledger is written by a separate component from the one the
   Auditor reads.
10. **Budgets.** Steps, tool calls, tokens, wall-clock, per-call timeouts (model + tool),
    denial and repeat caps. Daily cloud-spend cap with atomic reserve-before-call accounting shared
    across concurrent runs; exhaustion -> `budget_exhausted` with partial evidence.
11. **Separation of duties.** Writer != Reviewer (fresh context, different tier when possible);
    Auditor independent and exclusive; specs are code, spec_hash logged; no runtime spec edits.
12. **Concurrency.** File locks + atomic write-then-rename for `tasks`/`drafts`; per-run ordered ledger
    appends with a single writer; Auditor exclusivity lock.

## 7. New capability layer (L1)

| MCP server | Tools | Tier | Notes |
|---|---|---|---|
| `audit_reader` | `audit_verify_ledgers`, `audit_security_snapshot`, `audit_recent_denials`, `audit_go_live_status`, `audit_verify_checkpoint` | READ_ONLY | Reads stores directly; returns check verdicts as structured data |
| `workspace_read` | `workspace_list`, `workspace_read_file` | READ_ONLY | ALLOWLIST of dirs + extensions; resolve real path after normalization (symlinks, `..`, case, Windows alternate streams); size cap; explicit deny of Eternal's own data dir (ledgers, vault, consent, sessions, keys, db) |
| `drafts` | `drafts_save`, `drafts_list`, `drafts_read` | WRITE / READ_ONLY | Narrow store; labels stored with draft; no send path |
| `tasks` | `tasks_create`, `tasks_update`, `tasks_list` | WRITE / READ_ONLY | Server-side closure rules (s4.5); caller identity passed through the token to the server |

Each follows existing pattern: manifest entry with Pydantic `output_schema`, `data.py` + `server.py`,
file-backed state, `sys.modules.pop("data", None)` guard, no float64 leakage, no bare dict schemas.

## 8. Package layout

```
orchestration/
  contracts.py      AgentSpec, Task, StepRecord, AgentResult, Finding, Budget, Labels, ConsentGrant
  labels.py         provenance/sensitivity join rules, untrusted wrapper
  capability.py     CapabilityIssuer + CapabilityVerifier (verifier registered at Phase6 entry)
  broker.py         ToolBroker (only gateway holder), evidence registry, decoy flagging
  runtime.py        run loop, strict JSON parse, repair retry, budgets, kill checks, repeat detector
  ledger.py         run ledger + external checkpoint writer
  model_policy.py   label-aware cloud eligibility over LLMRouter, spend reservation
  consent.py        per-run grants, awaiting_confirmation handling
  agents/           auditor.py reviewer.py researcher.py writer.py task_issuer.py
  supervisor.py     classify, validate plan, dispatch, compose
  registry_ext.py   manifests + tiers for new MCP tools
  api.py            /agents/run, /agents/runs/{id}, /agents/specs, /agents/runs/{id}/confirm
mcp_servers/{audit_reader,workspace_read,drafts,tasks}/
tests/orchestration/
tests/orchestration/redteam/      injection corpus + canaries
scripts/orchestration/{demo_exit_criteria,mcp_e2e,http_e2e}.py
docs/ORCHESTRATION_README.md
```

## 9. Testing strategy

1. **Unit (scripted fake model):** allowlist denial, tier denial, each budget type, malformed JSON
   repair-then-fail, schema-invalid output, kill before/after generate and before dispatch, injection
   in a tool result doesn't change plan, fabricated evidence_ref rejected, decoy not cited, denial cap,
   repeat cap, token expiry/out-of-scope/revoked, label join never decreases, cloud blocked when
   context contains internal/secret, no silent local->cloud fallthrough.
2. **Invariant tests (tripwires, not proof):** AST: only `broker.py` references a gateway; no agent
   module imports a provider/MCP client/`subprocess`/`importlib`/`__import__`/`eval`; no spec lists a
   SENSITIVE tool; nothing in `eternal_core` imports `orchestration`; no spec has both `web_search`
   and a RAG tool. Runtime test supplements AST (attempt dynamic import path -> still denied by token).
3. **Security/adversarial:** red-team injection corpus with canary tokens against the REAL local
   model (second-order via tasks/drafts, exfil via search queries, cloud-compose leak of local-only
   findings, voice-origin laundering); RCAP CONTAIN-from-retry test; double-tamper (entry + head) caught
   by checkpoint; workspace_read path-escape suite (symlink, `..`, case, ADS, denylisted data dir).
4. **Concurrency & recovery:** parallel runs on `tasks`/`drafts`, ledger ordering, Auditor
   exclusivity, crash mid-run -> orphaned `running` reconciled at startup, partial ledger write recovery.
5. **Narrative demo per agent:** allowed action succeeds, forbidden action denied by REAL chain,
   ledger + checkpoint verify.
6. **Live MCP subprocess e2e and HTTP daemon e2e** for four servers and `/agents/*` (wire-level bugs
   have been the real finds in past phases).
7. **Model-quality evals** (rubric, local vs cloud) reported separately, not build pass/fail.

## 10. Build order & exit criteria

| Step | Deliverable | Exit criterion |
|---|---|---|
| O0 | contracts, labels, capability issuer/verifier, broker + evidence registry, runtime, consent, run ledger + checkpoint, invariant tests | fake-model run passes unit + invariant tests; agent call without valid token denied at gateway; broker sole gateway holder; ledger checkpoint verifies |
| O1 | `audit_reader` + Auditor | reports every chain + checkpoint; tampered ledger (incl. double tamper) -> high-severity finding |
| O2 | Researcher (web / docs modes) | sources resolve to registry; injected web text can't change behaviour or leak internal data |
| O3 | `workspace_read` + Reviewer | reviews a real diff with resolvable file:line; path-escape suite and data-dir denial pass |
| O4 | `drafts` + Writer (+ review loop) | draft saved, not sendable; loop <= 2 rounds; Writer != Reviewer enforced |
| O5 | `tasks` + Task Issuer | goal -> persisted tasks with deps; tainted tasks need approval; human-owned closure server-enforced |
| O6 | Supervisor | multi-agent request completes; depth/task/budget caps hold; mid-run kill stops everything; cloud compose respects labels |
| O7 | API + docs + four-way validation from fresh extraction | all layers green; README counts match real runs |

## 11. Decisions (DEFAULT applies if you don't override)

1. **Task Issuer meaning.** DEFAULT: both tracked tasks (for agents/you) and human reminders; no
   autonomous dispatch in v1.
2. **Local model capability.** DEFAULT: Auditor/Reviewer local-only; Researcher/Writer may use cloud
   within label limits; strict-JSON failures use repair retry then degraded path. Override: require a
   larger local model, or constrained decoding.
3. **Reviewer egress.** DEFAULT: local-only always.
4. **Spend cap.** DEFAULT: configurable daily cloud cap, enforced via atomic reservation; you set the
   value (placeholder 2 USD/day).
5. **Cloud eligibility by label.** DEFAULT: `public` and `internal` (non-PII) may go to cloud for
   Researcher/Writer; `secret` and any PII-bearing profile data never; Writer with profile data is
   local unless you opt a run in explicitly.
6. **Writer sink.** DEFAULT: local drafts store only for v1.
7. **Reviewer test execution.** DEFAULT: deferred (sandbox needed).
8. **Capability tokens touch L0.** DEFAULT: yes, the single additive `CapabilityVerifier`; existing
   gateways unchanged.
9. **Consent for unattended runs.** DEFAULT: per-run scoped grants approved at run start via the
   non-voice channel; otherwise `awaiting_confirmation`.
10. **Taint policy.** DEFAULT: `web`/`tool_output`/`agent_output`/`rag_internal`-derived content blocks
    auto-dispatch and requires human approval; `web` provenance additionally blocks any `secret`/
    `internal` data from joining the run.
11. **Checkpoint key/location.** DEFAULT: signed checkpoint with a key outside the agent/runtime
    process plus an off-box copy; you choose where the key and copy live.

## 12. Risks (residual)

- Small local model may fail JSON protocol often -> repair retry, degraded path, decision 2.
- Label discipline depends on every new tool declaring labels correctly -> manifest requires
  `sensitivity` + `provenance` fields; missing = registration error.
- Token verifier is new security-critical code -> gets the heaviest adversarial tests in O0.
- New write tools widen the surface slightly -> narrow stores, per-run consent, no execution path from
  stored data, closure rules server-side.
- Latency from multiple model calls -> budgets, parallel READ_ONLY tasks.
- AST tests are bypassable (dynamic imports) -> they're tripwires; the token verifier is the real
  control.
- Scope creep into SENSITIVE tools -> invariant test makes it a deliberate reviewed change.
- Same-model Writer/Reviewer weakens separation -> different tier preferred; logged when unavailable.

## 13. Invariants to encode as tests (checklist for O0)

- I1 No gateway call without a valid, unexpired, in-scope capability token.
- I2 Only `broker.py` references a gateway.
- I3 No agent call is ever FASTLANE.
- I4 Labels never decrease; cloud eligibility = min over context, enforced in router.
- I5 No run holds both `web_search` and RAG.
- I6 Every Finding evidence_ref and Researcher source URL resolves in that run's registry.
- I7 Decoy responses are never evidence.
- I8 Model output cannot lower a check-assigned severity or flip a check result.
- I9 Kill state checked before generate, after generate, before dispatch; tokens revoked on kill.
- I10 Voice-origin requests keep the voice gate on downstream WRITE.
- I11 Human-owned tasks closable only by human identity, enforced server-side.
- I12 Untrusted-derived tasks are never dispatched without human approval.
- I13 Run ledger head matches external checkpoint; mismatch is a high-severity audit finding.
- I14 Plan `allowed_tools_subset`/`budget_slice` are code-computed subsets of the spec.
- I15 No spec lists a SENSITIVE tool; `eternal_core` never imports `orchestration`.

---

## 14. v2.1 amendments (checked against the code) and answered decisions

Where v2 and the code disagreed, the code wins; each item below says what changed in the plan.

**A1. Attribution identity is per agent, not per run.** v2 s3 set `source_label=agent:<name>:<run_id>`.
RCAP's PERIMETER rate limits and ATTRIBUTE hard-block both key on `source_label`
(`rcap_p/gateway.py`), so a per-run label hands every new run a fresh bucket and a clean record —
a flagged agent could reset itself by starting a new run. Fix: `source_label = agent:<name>`
(stable). `run_id` goes in the explanation prefix (`[run <id>]`) and in every run-ledger entry.

**A2. Capability token transport.** `WriteCallRequest` (Phase 4 dataclass) has no token field and
`Phase6Gateway.call_tool` just forwards the request. v2's "verifier at Phase6 entry" is therefore
built as an additive wrapper, not an edit: `AgentCallRequest(WriteCallRequest)` adds
`capability_token`; `CapabilityGateway` verifies it and then delegates to `Phase6Gateway` unchanged.
Existing gateways still read only base fields. (O0b; test that the subclass flows through all four.)

**A3. Session binding.** The whole chain is keyed on the human `session_id` (QAP-P state, trust,
ROTATE token per (session, tool)). So agent calls carry the parent `session_id`; the issuer calls
`grant_tool_permission` once per in-scope tool at run start; ROTATE tokens live only inside the
broker and never enter model context. A quarantined or killed session therefore stops agent calls
through the existing Phase 5 ACTIVE-session guard, with no new mechanism.

**A4. Identity transport to MCP servers (your answer: signed envelope).** The stateless MCP executor
forwards only tool name + arguments, and the agent controls its own arguments. The broker rejects
any agent argument whose key starts with `_` (reserved); the capability layer then injects
`_identity = {identity, run_id, exp, sig}` (HMAC). `tasks`/`drafts` servers verify it with a key
supplied via environment (not visible to agents) and reject calls without a valid envelope.
Human closure uses an envelope issued by the API layer from the authenticated session.

**A5. Agent confirmation channel.** Agent calls use `ConfirmationChannel.AUTOMATED`; if the request
originated by voice, `VOICE` is carried so the voice gate stays on downstream WRITE (I10).

**A6. Tool timeouts.** `Phase6CoreGateway` is synchronous (`asyncio.run`). The broker enforces
`per_call_timeout_s` by abandoning the worker thread and reporting `timeout`; it cannot kill the MCP
subprocess. True cancellation needs an async gateway path and is scheduled with O6 (kill TOCTOU).

**Answered decisions**
- Identity: signed envelope (A4). O0 split into O0a / O0b.
- O0a: contracts, labels, evidence registry, broker (fake gateway), runtime, label-aware model policy
  router + spend reservation, run ledger, unit + invariant tests.
- O0b (DONE): capability issuer/verifier + `AgentCallRequest`, per-run consent, signed external checkpoint, crash-safe
  spend reservations. Deviations: Ed25519 tokens instead of HMAC (public-key-only verifier); the wrapping code lives in
  a new top-level `agent_chain/` package because the tripwires forbid `orchestration/` from touching the chain.
  Signer: local Ed25519 under a separate OS user now, phone WebAuthn later. Off-box: second machine over Tailscale +
  private GitHub repo through `CommandSink`.
- Red-team: harness and scripted-model tests run in the build sandbox; the real-local-model corpus
  is run on your machine and results shared. The sandbox cannot reach Ollama, and docs will say so.
- Local model: undecided -> design for a small model (repair retry + degraded path), revisit later.

**Still open:** paid no-training provider and daily cloud-spend cap value (placeholder 2 USD/day, configurable), Task
Issuer reminder mechanism (needed for O5). Decided: INTERNAL-data agents use a paid no-training API; free tiers see
PUBLIC data only.
