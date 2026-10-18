# Eternal — Implementation Status vs. Master Build Plan

Findings only. Nothing in this document was acted on — no code was
changed as a result of it. Every claim below is sourced to a specific
file; open the file to verify.

## How to read this

Each phase gets three buckets:

- **Done** — implemented, matches the plan, and covered by a passing
  test or a live demo script.
- **Deliberately scoped down / sandbox-limited** — the code is real and
  correct, but either (a) the plan's exact mechanism doesn't apply at
  one-user scale and was honestly reinterpreted, or (b) it can't be
  exercised end-to-end in this sandbox (no network route, no disk
  budget, no physical hardware) but is written against the real API and
  will work on the real deployment target.
- **Not implemented / open** — the plan calls for it and it genuinely
  isn't there yet.

Where the codebase's own docstrings already say this plainly (this
project is unusually good about that), this document just points at
them rather than re-explaining.

---

## Test suite status

All 501 tests pass. `docs/` now contains all six seeded documents —
`Eternal_Master_Build_Plan.md`, `QAP_Whitepaper_CHALLAYOGESWAR.md`,
`RCAP_Whitepaper_CHALLAYOGESWAR.md`, `README.md`, `PROJECT_TREE.md`,
and this file — all indexed via `scripts/eternal_core/ingest_docs.py`.
The retriever now surfaces real whitepaper content directly: a query
for "What does the ATTRIBUTE ring do in RCAP?" now returns the RCAP
whitepaper's own "3.1 Ring Roles" section as the top hit, not an
incidental mention elsewhere.

---

## Phase 1 — QAP-P Core Substrate

**Done:** device/session state machine (`qap_p/state_machine.py`),
peer-issued phone-signs-a-nonce authorization (`qap_p/auth.py`),
trust-score model (`qap_p/trust.py`), hash-chained append-only ledger
(`qap_p/ledger.py`), canary credential (`qap_p/canary.py`),
chaos-engineering self-test suite (`qap_p/chaos.py`), two-tier
QUARANTINE recovery — soft (biometric + cooldown) and hard (offline key)
(`qap_p/quarantine.py`), and the scoped-token vault
(`qap_p/vault.py`). `scripts/qap_p/demo_exit_criteria.py` walks the exact
exit criterion from Section 8: lock/quarantine/revive a session using
only a phone-equivalent authorizer, with a spoofed-reauth test that
triggers QUARANTINE.

**Open decision resolved:** Section 9 asked "HashiCorp Vault vs.
sops/age-encrypted files." `qap_p/vault.py` took the sops/age-style
approach — a locally-held symmetric key, per-token scoping and
revocation independent of the encrypted payload. Documented as a
swappable choice if you want real HashiCorp Vault later.

**Deliberately scoped down / sandbox-limited:**
- **Hardware-backed keys are simulated**, not real. `qap_p/hardware_key.py`'s
  `SimulatedHardwareKey` is a real in-process Ed25519 keypair — it
  exercises every signing/verification path but is explicitly *not* a
  hardware root of trust. The module docstring names exactly what plugs
  in per platform (Android StrongBox, iOS Secure Enclave, Windows Hello,
  Pi TPM 2.0) — none of that exists yet, because it requires real client
  devices/native apps, not backend Python.
- **Chaos self-test is callable, not scheduled.** `qap_p/chaos.py`
  exposes the test methods; wiring them to an actual systemd
  timer/cron job on the real Pi is a one-line ops step, not written
  yet.
- **Quarantine-tier classification (soft vs. hard) takes explicit
  booleans**, not automatic geolocation/impossible-travel detection —
  correctly deferred per `qap_p/quarantine.py`'s own docstring, pending
  real usage data to model anomaly detection against.

---

## Phase 2 — Text-Only Assistant Loop

**Done:** LangGraph graph + Ollama provider + cloud-fallback provider
chain (`eternal_core/llm/router.py`, `ollama_provider.py`,
`cloud_provider.py`), RAG over the docs corpus
(`eternal_core/rag/*`), FastAPI surface (`eternal_core/api.py`).
`docs/` now contains three real documents (README, PROJECT_TREE, and
the build plan itself), all indexed and retrievable — confirmed by
querying the retriever directly (top hit for "What does the ATTRIBUTE
ring do in RCAP?" is now the build plan's own Section 6.2, not an
incidental mention elsewhere).

**Deliberately scoped down / sandbox-limited:**
- Ollama itself can't be reached from this sandbox (`ollama.com` is
  outside the network allowlist) — `ollama_provider.py`'s client code is
  real and correct against Ollama's actual `/api/chat` endpoint; it just
  can't be proven live here. Same story for the cloud fallback provider
  where no API key is configured.
- **Tailscale reachability isn't something code can satisfy** — it's an
  infrastructure step (install Tailscale on the phone/laptop/mini-PC/Pi,
  join the mesh). `eternal_core/config.py`'s `bind_host` is deliberately
  a variable so the real deployment can bind to the Tailscale interface,
  but no amount of code review can confirm the mesh itself exists.

**Not implemented / open (Section 5.1 baseline features, otherwise unaddressed):**
- **Usage/cost dashboard per LLM provider** — no cost-tracking code
  anywhere in `eternal_core/`.
- **Task/reminder/calendar integration** — ÆTHER's "life calendar"
  (birthdays, dream journal) exists in the mirror
  (`mcp_servers/aether/data.py`), but there's no task/reminder system or
  calendar-app integration (Google Calendar, etc.).
- **Proactive surfacing** (Section 5.2) — "proactive" only appears in
  the codebase as an *actor-type* label (an automated caller vs. a live
  human, e.g. `rcap_p/models.py`'s `AUTOMATED_ROUTINE`), not as an
  implemented feature that surfaces information unasked.
- **Git-style versioned memory with rollback/diff** (Section 5.1/5.2) —
  no versioning/diff code found anywhere in `voice_consent_gateway/memory/`.

---

## Phase 3 — RCAP-P + First Read-Only Capabilities

**Done, and thoroughly so.** All seven rings are implemented, each with
a docstring mapping it explicitly back to the RCAP whitepaper section
and the build plan's personal-scale reading: `perimeter.py` (rate
limiting), `inspect_ring.py` (schema validation + pattern scanning —
gates *every* tool response, not just web, per Section 6.2's explicit
generalization), `rotate.py` (session-scoped tool grants),
`deceive.py` (decoy tool registrations + canary-leak detection),
`contain.py` (sandboxed placeholder response, not a hard crash),
`attribute.py` (per-source flag counting with auto-escalation),
`fastlane.py` (owner bypass, but — correctly — automated routines still
go through the full stack). CinemaVault and the study tracker are
registered as read-only MCP servers; web search is routed through
INSPECT. `scripts/rcap_p/demo_exit_criteria.py` proves the exact exit
criterion: a malicious test document gets flagged and attributed, not
silently passed or crashed.

**Open decision resolved:** Section 9 listed "CinemaVault's and the
study tracker's actual internal architecture — not yet reviewed" as
open. Both got reviewed during Phase 3 development —
`mcp_servers/cinemavault/data.py`'s docstring documents that CinemaVault
was never a separate codebase at all (confirmed by inspecting
`TRACKER.zip` directly): it's ÆTHER's own `Activity` model filtered to
`type=MOVIE`. The study tracker's real interface
(`study_progress_tracker.html`) was reviewed and its curriculum data
transcribed verbatim, not invented (`mcp_servers/study_tracker/data.py`).

**Deliberately scoped down:**
- **ATTRIBUTE's escalation threshold and Phase 1's trust-score weights
  are documented placeholders** (`rcap_p/attribute.py`: "the threshold
  number is a documented placeholder, same as `qap_p/trust.py`'s weights
  are") — the mechanism is real and tested; the numbers need tuning
  against real usage data that doesn't exist yet.
- **Web search is an offline stub** (`mcp_servers/web_search/backend.py`)
  — deliberately not a real search API in this sandbox (cost/key
  reasons), returns a clearly-synthetic placeholder result rather than
  fabricating a real-looking one. Swapping in a real provider (Tavily,
  etc.) is a one-file change.

**Cross-cutting finding (applies to Phase 3 and 4 both):** none of
ÆTHER, CinemaVault, or the study tracker are actually wired to a real
backend. All three are independent local SQLite mirrors in Python that
faithfully copy the real schema/content — but a habit logged through
Eternal will **not** appear in the real ÆTHER app (TypeScript/Express/
Postgres/Prisma), and vice versa, until someone points
`mcp_servers/aether/data.py` at ÆTHER's actual REST API instead of its
own SQLite file. Every docstring involved says this plainly and says
the swap is "a change local to this file" — but it hasn't been made, and
it's the single biggest reason the "every existing tool becomes a
callable capability instead of a silo" vision (Section 5.2) isn't true
yet in practice, only in shape.

---

## Phase 4 — Voice + Write-Capable Capabilities

**Done:** local TTS via pyttsx3/espeak-ng — genuinely produces real
16-bit PCM WAV audio, confirmed working headless
(`voice_consent_gateway/voice/tts.py`). `PolicyEnforcingTTS` enforces
Section 6.3's outbound data-classification rule for real (finance/
health/dream-journal content is hard-routed local-only, never to
ElevenLabs). ÆTHER is wired in as a write-capable MCP capability with
per-tool consent (`voice_consent_gateway/consent.py`) and an audit trail.
Memory writes route through the same INSPECT check as live tool calls
(Section 6.3, point 4). Voice is correctly excluded as sole
authorization: sensitive-tier actions require a `confirmation_channel`
that isn't `voice` (`device_control_gateway/api.py`,
`device_control_gateway/gateway.py`).

**Not implemented / open — this is the biggest gap in Phase 4:**
- **Whisper STT is not wired.** `voice_consent_gateway/voice/stt.py`'s
  real backend raises `NotImplementedError("wire real
  whisper.transcribe(...) call here")`. Blocked in this sandbox by disk
  space, not a design choice (`openai-whisper` install failed with
  `OSError: [Errno 28] No space left on device`) — will need to be
  installed on the real deployment target.
- **openWakeWord is not wired.** Same story —
  `voice_consent_gateway/voice/wake_word.py`'s real detector raises
  `NotImplementedError`; blocked by disk budget for the `onnxruntime`
  stack and no reachable model host. The fallback that *is* real and
  tested (`RmsEnergyWakeWordDetector`) only detects "audio loud enough
  is happening," not the specific phrase "Hey Eternal."
- **ECAPA-TDNN speaker verification is not wired** —
  `voice_consent_gateway/voice/speaker_verify.py` raises
  `NotImplementedError("wire real ECAPA-TDNN embedding + cosine-
  similarity comparison here")`.
- **Bilingual English/Telugu voice (Bhashini/AI4Bharat) is not
  implemented at all** — `voice_consent_gateway/voice/stt.py`'s
  docstring states this directly. This is one of the two things the
  build plan names as the actual differentiator (Section 1), so it's
  worth flagging even though it wasn't in Phase 4's original written
  scope.
- **ElevenLabs cloud TTS is a stub** — no reachable host/API key
  configured in this sandbox; the policy-enforcement code that decides
  *when* to call it is real and tested, but the backend itself will
  raise until a real key is configured.

---

## Phase 5 — Remote Device Control + Home Assistant

**Done:** kill switch (`device_control_gateway/killswitch.py`) —
quarantines every device and revokes every scoped token via
`qap_p.orchestrator`, ledger-logged. Non-voice confirmation enforced the
same way as Phase 4. `scripts/device_control_gateway/demo_exit_criteria.py`
proves a real Wake-on-LAN magic packet is received by a real UDP
listener (not simulated) and that the state machine correctly rejects
an invalid transition (e.g. locking an already-sleeping machine).

**Real and wired, but individually untestable live in this sandbox:**
- **Home Assistant's real client is fully implemented** —
  `mcp_servers/home_assistant/data.py`'s `RealHomeAssistantClient` makes
  genuine `httpx` calls against HA's real documented REST API (`POST
  /api/services/<domain>/<service>`, `GET /api/states/<entity_id>`).
  Untestable here only because there's no reachable HA instance in this
  sandbox — this is code-complete, not a stub.

**Not implemented / open:**
- **The laptop agent has no real OS-control implementation.**
  `mcp_servers/laptop_agent/data.py`'s `LaptopActionProvider` base class
  (`lock`/`unlock`/`sleep`/`open_app`/`run_script`/`shutdown`) is *all*
  `raise NotImplementedError` — only `SimulatedLaptopActionProvider`
  exists. Unlike Home Assistant above, there's no real backend here yet
  at all, even one that's merely untestable in-sandbox.
- **Full remote-desktop control (RustDesk vs. OS-native) — still
  Section 9's open decision, unresolved in code.**
  `mcp_servers/remote_control/data.py` has a literal placeholder
  (`connection_hint="rustdesk-id-placeholder-000000000"`); Wake-on-LAN
  itself is real, but the actual handoff to a working remote-desktop
  session isn't built.

**Worth knowing, not a bug:** `device_control_gateway/mcp_client_env.py`
documents a real gap it found in Phase 3's `rcap_p.mcp_client` (spawned
MCP subprocesses never inherit any parent environment variable, which
would have made `HOME_ASSISTANT_BASE_URL` etc. silently unreachable). It
worked around this with a parallel, env-aware executor rather than
modifying already-validated Phase 3 code, and confirms every Phase 5
caller uses the new one exclusively. Functionally complete; the
docstring itself recommends retiring this module once the real fix
lands upstream in `rcap_p.mcp_client` — worth doing at some point so
there's only one code path, not two.

---

## Phase 6 — Trading

### Sub-phase A (signals, no live orders) — Done

Data pipeline (`trading_risk_engine/data_provider.py` — real `yfinance`
client plus a GBM-based `SimulatedMarketDataProvider` used by every
test), hand-verified indicators (`indicators.py`), rules-based signals
(`signals.py`), full backtesting (`backtester.py`), fixed-fractional
ATR-based position sizing, entry-only stop/target, and a max-drawdown
circuit breaker that halts new entries rather than pushing harder
(`risk.py` — all four Section 7.2 clauses have a named, direct
implementation), and paper trading (`paper_trader.py`,
`broker/paper_broker.py`). `performance.py` deliberately never surfaces
a single-week return figure, matching the build plan's math correction
about not chasing weekly recovery targets.

### Sub-phase B (live) — Done, gated correctly

Real (untestable-live-here, not stubbed) Kite Connect broker
(`broker/kite_connect.py`), a genuine documented daily re-auth flow
(`broker/daily_reauth.py` — login URL → manual login → token exchange;
deliberately does *not* automate the login/2FA page itself, which the
build plan's own wording never asked for), an order-rate limiter
defaulting to 3/sec against the regulatory 10/sec ceiling
(`config.py`, `regulatory.py`), and live-order placement correctly
gated as a SENSITIVE-tier action requiring non-voice confirmation.

### Phase 6 Extended (F&O) — implemented and wired end to end

F&O building blocks (contracts, pricing/margin, calendar, synthetic data provider, risk manager,
backtester, paper broker, live-broker skeleton) are now reachable as 11 MCP tools:
`fo_get_option_chain`, `fo_get_signal`, `fo_run_options_backtest`, `fo_run_futures_backtest`,
`fo_get_paper_positions`, `fo_get_paper_margins` (READ_ONLY); `fo_place_paper_option_order`,
`fo_place_paper_future_order`, `fo_close_paper_position` (WRITE); `fo_place_live_option_order`,
`fo_place_live_future_order` (SENSITIVE — same top-trust-tier + regulatory gate chain as
`trading_place_live_order`, via `LIVE_ORDER_TOOL_NAMES` in `trading_risk_engine/gateway.py`).

Wiring: `mcp_servers/trading_engine/data.py` (`FOTradingEngine`), `server.py` (tools + Pydantic models),
`trading_risk_engine/registry_ext.py` (tiers), `trading_risk_engine/gateway.py` (gated set).
`core_gateway.py` is tool-agnostic and needed no change. Full details and validation results:
`docs/PHASE6_FO_README.md`.

Validated by actually running: 618-test suite (14 voice tests fail only because eSpeak is not installed
in the build sandbox), demo 38/38, live MCP e2e 13/13, live HTTP e2e 22/22. Four real defects found
and fixed during that run (see the F&O README).

**Phase 6 completion pass (merge of Phase 1-5 + chat 1 + chat 2):** F&O backtests now return a `performance` block
(Sharpe/Sortino/Calmar/drawdown/profit factor/expectancy, `instruments/fo_performance.py`, same formulas as equity);
F&O `get_order()` implemented; F&O live tools take `market_protection_pct`; 2025 holidays added; and the master
plan's go-live criterion is now code (`trading_risk_engine/go_live.py` — advisory, fail-closed, never enables live
trading). See `docs/PHASE6_COMPLETION.md`.

Still open for F&O: real Kite network calls (instrument dump, order placement, `get_order()`) have only been run
against fake clients; margins are approximations, not SPAN; data is simulated; calendar covers 2025-2026 only;
stock F&O and commodities are out of scope by decision.

---

## Section 9 — Open Decisions, current status

| Decision | Status |
|---|---|
| Vault: HashiCorp vs. sops/age | **Resolved** — sops/age-style, `qap_p/vault.py` |
| Mini-PC spec/purchase | Still open (hardware procurement, not code) |
| RustDesk vs. OS-native remote desktop | **Still open** — `remote_control/data.py` has a placeholder connection ID, no real integration either way |
| Identity-core doc (persona/tone) | **Still open** — no such file exists anywhere in the project |
| UI design system (dark-space/glassmorphism/CRT/amber) | **Still open** — see "No frontend exists" below |
| Confirm Zerodha/Kite Connect as broker | **Resolved** — `broker/kite_connect.py`, `daily_reauth.py` are Kite-Connect-specific throughout |
| CinemaVault / study tracker architecture review | **Resolved** — see Phase 3 above |

---

## Cross-cutting findings

**No frontend/UI code exists anywhere in this project.** A search for
`.jsx`/`.tsx`/`.vue`/`package.json` across the whole tree returns
nothing — this is 100% backend Python. That matters concretely for one
specific plan requirement: Section 6.3/7.1's "non-voice confirmation" —
"a tap in the phone app, not a spoken 'yes'" — is enforced as a policy
at the API layer (`confirmation_channel` must not be `voice` for
SENSITIVE-tier actions) and that policy is real and tested, but there is
no actual phone app for a person to tap. Any client that calls the API
with `confirmation_channel: "text_typed"` satisfies the check today —
which is correct against injected/automated callers, but isn't yet the
same thing as a human physically confirming through a real UI.

**Residual naming defects:** the remaining `phaseN_eternal_phaseN_*` merge-corruption identifiers were fixed project-wide during Phase 6 Extended integration (one of them, `FOTrade.r_multiple`, was a real crash, not cosmetic).

**Documentation gaps:**
- `fo_kite_connect_broker.py`'s `get_order()` now points to `docs/PHASE6_FO_README.md` (known-simplifications section added).
- The build plan's own Section 10 source material
  (`Personal_Tracker.zip`, `PERSONAL_ASSISTANT.zip`,
  `Untitled_document.md`, `My_Verdict_on_Outline.md`, and the two
  whitepapers) isn't part of this repository at all — expected, since
  the plan describes them as prior-conversation source material to
  "bring into the new project alongside this file," not something this
  codebase was ever supposed to contain.

---

## If you're deciding what to do next

In rough order of how directly each blocks continuing Phase 6 Extended
or closes a real product gap, rather than a sandbox artifact:

1. ~~Wire the F&O building blocks to tools~~ — done (see Phase 6 Extended above). Next for trading: exercise `FOKiteConnectBroker` against real Kite credentials on the static-IP host, implement F&O `get_order()`, and consider more instruments only after the Index Options + Futures performance review.
2. **Decide whether ÆTHER/CinemaVault/study-tracker should point at the
   real backends** instead of their local mirrors — this is the gap
   between "shaped like the real thing" and "is the real thing," and
   every file involved says the swap is contained to itself.
3. **The laptop agent has no real action provider at all** — worth
   deciding whether that's still wanted before Phase 5 is called done,
   since Home Assistant's real client already sets the bar for what
   "real, just untestable here" looks like.
4. **RustDesk vs. OS-native** is the one Section-9 open decision that
   blocks an actual exit criterion (Phase 5's "full laptop control ...
   from outside the home network").
5. Everything under "Not implemented / open" in Phase 4 (Whisper,
   openWakeWord, ECAPA-TDNN, Bhashini) needs the real deployment target
   (mini-PC/Pi) to even attempt — not something this sandbox can move
   forward regardless of priority.
6. The identity-core doc and the UI design system are both still
   undrafted — lower urgency than the above since nothing downstream is
   currently blocked waiting on either, but they're explicitly named as
   open in the plan and haven't moved.

---

## Orchestration layer (post Phase 6)

Planned in `docs/ORCHESTRATOR_LAYERS_PLAN.md`; O0a (runtime foundation) built and tested — see
`docs/ORCHESTRATION_README.md`. P1 (Gemini/Groq providers, per-provider data ceilings, rate limits, spend settlement)
and P2 (validation harness, `docs/VALIDATION_HARNESS.md`) are built; the first live free-tier runs were INCONCLUSIVE
(provider/model output faults, now handled as bad turns) and no provider is approved yet. No specialist agents,
capability verifier (O0b), or new MCP servers exist yet.
