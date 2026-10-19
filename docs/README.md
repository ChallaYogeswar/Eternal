# Eternal AI Assistant

The canonical, combined project tree for Eternal Phases 1–6. The detailed
layout is documented in [docs/PROJECT_TREE.md](PROJECT_TREE.md).

## Runtime packages

- `qap_p/` — identity, sessions, trust, vault, and audit ledger (Phase 1)
- `eternal_core/` — text assistant, RAG, and LLM routing (Phase 2)
- `rcap_p/` — inspection, containment, attribution, and MCP gateway (Phase 3)
- `voice_consent_gateway/` — voice, consent, memory, and write-capable gateway (Phase 4)
- `device_control_gateway/` — remote-device control, Home Assistant, and kill switch (Phase 5)
- `trading_risk_engine/` — trading signals, paper trading, risk, and broker gates (Phase 6)
- `mcp_servers/` — MCP servers for every capability
- `scripts/<package>/` — per-package demonstrations and live proofs
- `tests/<package>/` — per-package automated test suite
- `docs/` — project documentation (Phase 6 F&O: [PHASE6_FO_README.md](PHASE6_FO_README.md); status: [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md))

Every package name above is the real, final name: it is what's on disk,
what every import statement uses, and what every doc references. There
is no separate "semantic" name layered on top.

## Setup

```powershell
cd <path-to-your-Eternal-folder>
python -m pip install -r requirements.txt -c constraints.txt
$env:PYTHONPATH = (Get-Location).Path
```

Voice-pipeline tests (`tests/voice_consent_gateway/test_stt.py`,
`test_tts.py`, `test_wake_word.py`, `test_speaker_verify.py`) additionally
need `espeak-ng` on `PATH` (`apt-get install espeak-ng` on Linux, or see
eSpeak NG's own install instructions on macOS/Windows). Without it those
specific tests fail with a clear `EspeakNotAvailableError`/`RuntimeError`;
nothing else in the suite depends on it.

## Core validation

```powershell
$env:PYTHONPATH = (Get-Location).Path
python -m pytest tests/qap_p -q
python -m pytest tests/eternal_core -q
python -m pytest tests/rcap_p -q
python -m pytest tests/voice_consent_gateway -q
python -m pytest tests/device_control_gateway -q
python -m pytest tests/trading_risk_engine -q
```

`pytest.ini` sets `asyncio_mode = auto` at the project root — this is
required for the async gateway-integration tests under `tests/rcap_p/`
and `tests/voice_consent_gateway/` and must ship alongside the code
(it was missing before this cleanup pass; see below).

The `eternal_core` index expects its document corpus under the current
working folder or `ETERNAL_DOCS_DIR`. For normal `eternal_core`
validation, run from this project root after setting that variable to
the desired docs directory.

`docs/` now contains all three documents the seeded-corpus test expects
— `Eternal_Master_Build_Plan.md`, `QAP_Whitepaper_CHALLAYOGESWAR.md`,
and `RCAP_Whitepaper_CHALLAYOGESWAR.md` — all indexed via
`scripts/eternal_core/ingest_docs.py`. The full suite (501 tests) passes.

For a full phase-by-phase comparison of this codebase against the build
plan — what's implemented, what's deliberately scoped down for the
sandbox, and what's still genuinely open — see
[docs/IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

## Services

```powershell
uvicorn eternal_core.api:app --host 127.0.0.1 --port 8422
uvicorn rcap_p.api:app --host 127.0.0.1 --port 8423
uvicorn voice_consent_gateway.api:app --host 127.0.0.1 --port 8424
uvicorn device_control_gateway.api:app --host 127.0.0.1 --port 8425
uvicorn trading_risk_engine.api:app --host 127.0.0.1 --port 8426
```

Bind services only to loopback or a private Tailscale address in
deployment. Do not expose these development surfaces directly to the
public internet.

## Migration notes (this cleanup pass)

The project arrived as six separately-built phase zips merged into one
tree. The merge preserved working imports but left the naming and a few
wiring paths inconsistent. This pass renamed everything to its real name
and fixed what the renaming (and a prior automated rename attempt)
had broken. In order of impact:

**Fixed wiring bugs (behavior changes):**

- **Added the missing `pytest.ini`** (`asyncio_mode = auto`). Without
  it, 14 of `rcap_p`'s async gateway-integration tests failed outright
  with "async def functions are not natively supported" — not a code
  bug, just a missing project-root config file.
- **`device_control_gateway/killswitch.py` and `trust_bridge.py`** called
  `orchestrator.<mangled-name>` instead of `orchestrator.store` — a real
  production bug from a prior bad rename pass, not just a test bug. The
  kill switch (and the top-trust-tier lookup it depends on) would have
  raised `AttributeError` the first time either ran for real.
  `qap_p/orchestrator.py` itself was never touched and was always
  correct.
- **A whole Phase 4 test file was invisible to pytest.** The merge left
  it named `phase4_eternal_tests_test_gateway_integration.py`, which
  doesn't match pytest's `test_*.py` discovery pattern, so its 12 tests
  never ran and pytest never reported an error either — it just silently
  collected 86 Phase 4 tests instead of 98. It's now
  `tests/voice_consent_gateway/test_gateway_integration.py`, and all 12
  pass.
- **`trading_risk_engine/instruments/fo_kite_connect_broker.py` and
  `fo_backtester.py`** imported from `..phase1_qap_p_models` and
  `..phase1_qap_p_config`, neither of which exists — these two modules
  could not be imported at all. Nothing in the current test suite
  exercises the F&O checkpoint work yet, which is why this was silent;
  it would have surfaced the moment Phase 6 Extended wired them in.
  Fixed to import from `..models` / `..config`.
- **`scripts/device_control_gateway/demo_exit_criteria.py` and
  `scripts/trading_risk_engine/demo_exit_criteria.py`** had the same
  broken-import pattern against `qap_p`/`rcap_p` (`qap_p.phase1_qap_p_config`,
  `rcap_p.phase3_rcap_p_gateway`, etc.) — neither script could run.
  Fixed the same way.
- **`tests/device_control_gateway/test_registry_ext.py`** computed its
  `mcp_servers/` path one directory level too shallow
  (`os.path.join(os.path.dirname(__file__), "..", "mcp_servers")`),
  so `test_server_keys_point_at_real_directories` failed even once the
  attribute-name bug above it was fixed. Corrected to `"..", ".."`.
- **`trading_risk_engine/broker/paper.py` vs. `paper_broker.py`** — the
  real paper-broker implementation had been renamed to `paper.py` during
  the merge, with a one-line `from .paper import *` shim left behind as
  `paper_broker.py`. Some call sites imported the real module by its
  temporary name, others imported the shim, inconsistently.
  Consolidated into a single `paper_broker.py` (the real implementation)
  and repointed every caller.

**Renamed for clarity (no behavior change — same object, clearer name):**

A prior automated rename pass had also corrupted a number of in-code
attribute names, imports, and docstring mentions by substituting
nonsensical compound names (e.g. `self.phase1_qap_p_config` for what is
just `self.config`; a property literally named
`phase3_rcap_p_mcp_servers_cinemavault_data_class` for what is just
`data_class`). Most instances were internally self-consistent (same
wrong name used at both definition and call sites) so they didn't crash
anything — they were just confusing and actively misleading about what
the code does. All were restored to their plain, correct names across
every file that referenced them, including in docstrings.

**Structural renames:**

- `phase4/` → `voice_consent_gateway/`, `phase5/` → `device_control_gateway/`,
  `phase6/` → `trading_risk_engine/`, and their `scripts/`/`tests/`
  counterparts, plus `scripts/phase1..3` → `scripts/qap_p`,
  `scripts/eternal_core`, `scripts/rcap_p` (and the same for `tests/`).
- Every `tests/<package>/` now has an `__init__.py`, so pytest always
  imports test modules by full dotted path — this also fixed two test
  files whose bare `from conftest import ...` had started resolving to
  the wrong `conftest.py` after the packages above were made consistent
  (rewritten as explicit `from .conftest import ...`).
- Stripped redundant merge-artifact prefixes from filenames, e.g.
  `phase6_eternal_tests_test_backtester.py` → `test_backtester.py`,
  `phase5_eternal_scripts_test_mcp_e2e.py` → `mcp_e2e.py`, restored
  `tests/phase1/test_phase1.py` to its original `test_qap_p.py`.
- Removed the ~200 stale, pre-merge "source path" header comments
  (e.g. `# phase6_eternal\phase6\gateway.py`) that
  no longer described where anything actually lives.

**Verified, not changed:** every runtime module and test in
`mcp_servers/`, `qap_p/`, `rcap_p/`, and `eternal_core/` imports and runs
correctly as-is — only the corrupted names above were touched inside
them. After all of the above, the full suite is 500 passed / 1 failed
(the missing-build-plan-document gap noted above) out of 501 tests,
versus 432 passed / 57 failed out of 489 before this pass (the total
rose by 12 once the invisible test file above was restored).


## Notes on running the tests

- Full suite: `python -m pytest tests -q` (from the `Eternal` directory, with `PYTHONPATH` set as above).
- The voice tests (`tests/voice_consent_gateway`: TTS, STT, wake word, speaker verification) need the eSpeak
  system library (`espeak-ng`) for `pyttsx3`; without it, those 14 tests fail while everything else passes.
- Live proofs: `scripts/trading_risk_engine/demo_exit_criteria.py`, `mcp_e2e.py`, and `http_e2e.py`
  (the HTTP one needs `uvicorn trading_risk_engine.api:app --port 8424` running with `RCAP_P_PROJECT_ROOT` set).
