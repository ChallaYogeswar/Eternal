# Eternal Project Tree

This folder is the combined runtime for Eternal, all phases through
Phase 6. Every package below has one real, permanent name — there is no
separate "semantic" name layered on top of a numbered one anymore.
Where a package began life as a numbered build phase, that history is
noted in parentheses for context only; it is not part of the name.

```text
Eternal/
├── qap_p/                     Identity, trust, sessions, vault, ledger (Phase 1)
├── eternal_core/              Assistant graph, LLM routing, RAG (Phase 2)
├── rcap_p/                    Inspection, containment, attribution, MCP gateway (Phase 3)
├── voice_consent_gateway/     Voice, consent, memory, write gateway (Phase 4)
│   ├── memory/                 Structured memory storage and gated writes
│   └── voice/                  Wake word, STT, speaker verification, intent, TTS
├── device_control_gateway/    Device control, Home Assistant, kill switch (Phase 5)
├── trading_risk_engine/       Signals, risk, backtesting, paper/live broker gates (Phase 6)
│   ├── broker/                  Paper broker, Kite Connect, daily re-authentication
│   └── instruments/             F&O contracts, pricing, expiry, margin, and backtesting
├── mcp_servers/                Standalone MCP capability servers
│   ├── aether/                  Tracker writes
│   ├── cinemavault/             Read-only movie library
│   ├── study_tracker/           Read-only curriculum tracker
│   ├── web_search/              Search capability
│   ├── laptop_agent/            Fixed laptop actions
│   ├── remote_control/          Wake-on-LAN and remote-control handoff
│   ├── home_assistant/          Home Assistant actions and state
│   ├── trading_engine/          Trading and paper-trading tools
│   ├── malicious_test/          Security-test fixture
│   └── sensitive_demo/          Sensitive-action security-test fixture
├── scripts/                    Manual demos and live end-to-end proofs, one folder per package above
│   ├── qap_p/ eternal_core/ rcap_p/ voice_consent_gateway/ device_control_gateway/ trading_risk_engine/
├── tests/                      Automated pytest suite, one package per package above
│   ├── qap_p/ eternal_core/ rcap_p/ voice_consent_gateway/ device_control_gateway/ trading_risk_engine/
├── data/                        Local runtime data, Chroma index, and SQLite state
├── docs/                        Project documentation and this tree
├── pytest.ini                   asyncio_mode = auto — required for the async gateway tests
├── README.md                    Setup, validation, and service commands
└── requirements.txt              Python dependencies
```

## Naming rules

- Every package has exactly one name, used everywhere: on disk, in
  imports, and in documentation. `qap_p` and `rcap_p` are the protocol
  names (QAP-P / RCAP-P) and stay short by design; the later layers are
  named for what they do (`voice_consent_gateway`,
  `device_control_gateway`, `trading_risk_engine`) rather than for the
  order they were built in.
- Runtime modules use short, consistent names within each package:
  `api.py`, `config.py`, `gateway.py`, `models.py`, `ledger.py`,
  `ledger_bridge.py`, `registry_ext.py`, `deployment.py`, and so on.
- `tests/<package>/` mirrors `<package>/` one-for-one and is itself a
  real Python package (every `tests/<package>/` has an `__init__.py`),
  so pytest always imports each test module by its full dotted path —
  no directory ever needs to be guessed onto `sys.path`, and two test
  files can safely share a filename (e.g. `test_gateway.py`) as long as
  they live in different packages.
- Test files always match pytest's `test_*.py` discovery pattern.
  (A prior merge step had left one Phase 4 test file named
  `phase4_eternal_tests_test_gateway_integration.py` — because it didn't
  start with `test_`, pytest silently never collected its 12 tests. That
  file is now `tests/voice_consent_gateway/test_gateway_integration.py`
  and those 12 tests run again.)
- MCP server data modules are imported from their real package path,
  e.g. `mcp_servers.home_assistant.data`.
- Generated caches (`__pycache__/`, `.pytest_cache/`) and local runtime
  state do not belong in source control.
- `data/` is intentional local state. Keep it for local development,
  but regenerate or back it up before deleting it.

## What changed in this pass

This structure replaces an earlier merged layout where `voice_consent_gateway`,
`device_control_gateway`, and `trading_risk_engine` were named `phase4`,
`phase5`, and `phase6`, and a number of files carried redundant
merge-artifact prefixes in their names (e.g.
`phase6_eternal_tests_test_backtester.py` instead of
`test_backtester.py`). See `README.md`'s "Migration notes" section for
the full list of renames and the wiring bugs that were fixed alongside
them.
