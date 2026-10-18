# Eternal Naming Guide

The final project uses purpose-based package names where practical and preserves stable names for established protocols.

| Package | Purpose |
| --- | --- |
| `qap_p` | QAP-P identity, device trust, sessions, vault, and audit |
| `eternal_core` | Assistant orchestration, LLM routing, and RAG |
| `rcap_p` | RCAP-P tool gateway, inspection, containment, and attribution |
| `voice_consent_gateway` | Phase 4 voice, consent, memory, and write controls |
| `device_control_gateway` | Phase 5 laptop, remote-control, Home Assistant, and kill-switch controls |
| `trading_risk_engine` | Phase 6 signals, risk, backtesting, paper trading, and live-order gates |
| `mcp_servers` | MCP capability server implementations |
| `scripts` | Demos, ingestion utilities, and end-to-end checks |
| `tests` | Automated tests grouped by package |
| `data` | Generated local runtime state; do not treat as source code |
| `docs` | Architecture plans, whitepapers, status, and completion reports |

## File conventions

- Use short, role-based Python module names such as `api.py`, `config.py`, `gateway.py`, `models.py`, and `data.py`.
- Name automated tests `test_<behavior>.py` and place them under the matching `tests/<package>/` directory.
- Name manual demos by behavior, such as `demo_exit_criteria.py`; keep live transport proofs separately identifiable as `mcp_e2e.py` or `http_e2e.py`.
- Keep protocol identifiers `QAP-P` and `RCAP-P` because they are established project concepts, not generic phase labels.
- Keep generated caches, SQLite databases, and vector indexes out of source control; preserve local `data/` when it contains user state.

The original folder names `phase4`, `phase5`, and `phase6` map to the final semantic package names `voice_consent_gateway`, `device_control_gateway`, and `trading_risk_engine`, respectively. Runtime imports and deployment scripts should use the final package names.
