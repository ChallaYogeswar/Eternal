# Eternal AI Assistant

Eternal is a personal assistant project organized into six build phases. `Eternal_final` is the canonical working folder; the phase package names are descriptive, while `qap_p` and `rcap_p` remain stable Python import names for compatibility.

## Project Layout

- `qap_p/` - Phase 1 identity, trust, sessions, vault, and audit ledger
- `eternal_core/` - Phase 2 assistant graph, LLM routing, and RAG
- `rcap_p/` - Phase 3 inspection, containment, attribution, and MCP gateway
- `voice_consent_gateway/` - Phase 4 voice, consent, memory, and write gateway
- `device_control_gateway/` - Phase 5 remote device control, Home Assistant, and kill switch
- `trading_risk_engine/` - Phase 6 trading signals, risk controls, backtesting, and brokers
- `orchestration/` - Sub-agent runtime, guarded tool broker, provider policy, and validation harness
- `mcp_servers/` - Standalone MCP capability servers
- `scripts/` - Demos and live end-to-end checks, organized by package/phase
- `tests/` - Automated tests organized by package
- `data/` - Local runtime state and generated indexes
- `docs/` - Build plan, whitepapers, implementation status, and completion reports

See [docs/PROJECT_TREE.md](docs/PROJECT_TREE.md), [docs/NAMING_GUIDE.md](docs/NAMING_GUIDE.md), and [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) for the detailed structure and implementation boundaries.

## Setup

From PowerShell:

```powershell
Set-Location "C:\Users\chall\Downloads\PROJECTS\ON PROCESS\Eternal AI Assistant\Eternal_final"
python -m pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path
```

Phase 4 audio-fixture and local TTS tests also require eSpeak NG installed and available on `PATH`.

## Tests

Run the complete suite:

```powershell
python -m pytest -q
```

Or validate one package at a time:

```powershell
python -m pytest tests/qap_p -q
python -m pytest tests/eternal_core -q
python -m pytest tests/rcap_p -q
python -m pytest tests/voice_consent_gateway -q
python -m pytest tests/device_control_gateway -q
python -m pytest tests/trading_risk_engine -q
python -m pytest tests/orchestration -q
```

## Orchestration Validation

The orchestration layer has an offline harness self-test and live provider checks. Set `GEMINI_API_KEY` and/or `GROQ_API_KEY` in the current shell using a local secret manager; do not commit keys or save them in project files.

```powershell
python -m orchestration.validation --selftest
python -m orchestration.validation --smoke
python -m orchestration.validation --run --repeats 3
```

The live run can take time under provider rate limits. Reports are written to `validation_reports/`.

## Local APIs

```powershell
uvicorn eternal_core.api:app --host 127.0.0.1 --port 8422
uvicorn rcap_p.api:app --host 127.0.0.1 --port 8423
uvicorn voice_consent_gateway.api:app --host 127.0.0.1 --port 8424
uvicorn device_control_gateway.api:app --host 127.0.0.1 --port 8425
uvicorn trading_risk_engine.api:app --host 127.0.0.1 --port 8426
```

Bind services only to loopback or a private Tailscale address. Do not expose development endpoints directly to the public internet.
