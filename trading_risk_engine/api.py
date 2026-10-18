"""
Phase 6 HTTP daemon — additive to Phase 5's, same pattern every phase
since Phase 4 has used: a real deployment runs ONE daemon (this one).

Mounts qap_p.api.app at /qap unchanged (same as Phase 5), reuses Phase
5's ENTIRE gateway stack (QAP-P orchestrator, RCAP-P gateway with a real
trust_lookup, Phase 4's consent/voice-gate, Phase 5's kill switch and
top-trust-tier gate) completely unmodified, and adds exactly two things:

1. `trading_engine` in the MCP server command map (trading_risk_engine/deployment.py)
   and tool registry (trading_risk_engine/registry_ext.py), so trading tools are
   reachable through the SAME /tools/call and /tools/grant routes every
   other tool already uses.
2. Phase6Gateway sitting in front of Phase5Gateway, adding the
   top-trust-tier + regulatory gate chain for `trading_place_live_order`
   only (see trading_risk_engine/gateway.py's module docstring for exactly what is,
   and is not, new here).

Everything else — kill switch, ledger, consent, the top-trust-tier
read-only check — is exposed directly from the SAME underlying objects
Phase 5 already constructed, not re-wrapped, because Phase 6 adds no new
behavior to any of them.
"""
import os as _os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from voice_consent_gateway.config import Config as Phase4Config
from voice_consent_gateway.consent import ConsentRegistry
from voice_consent_gateway.gateway import Phase4Gateway
from voice_consent_gateway.models import ConfirmationChannel, WriteCallRequest

from qap_p.api import app as qap_app
from qap_p.api import orchestrator
from rcap_p.config import Config as RcapConfig
from rcap_p.gateway import RCAPGateway
from rcap_p.mcp_client import build_tool_to_server_map

from device_control_gateway.config import Config as Phase5Config
from device_control_gateway.gateway import Phase5Gateway
from device_control_gateway.mcp_client_env import make_env_aware_stateless_executor
from device_control_gateway.trust_bridge import QapPTrustLookup
from device_control_gateway.trust_tier import TopTrustTierGate

from .config import Config
from .deployment import build_default_server_commands
from .gateway import Phase6Gateway
from .ledger_bridge import Ledger
from .regulatory import DailyReauthGate, IPChangeTracker, LiveTradingGate, OrderRateLimiter, StaticIPHostGate
from .registry_ext import build_registry

config = Config()
phase5_config = Phase5Config(data_dir=config.data_dir)
phase4_config = Phase4Config(data_dir=config.data_dir)
ledger = Ledger(config.ledger_db_path)
registry = build_registry()

rcap_project_root = Path(_os.environ.get("RCAP_P_PROJECT_ROOT", Path(__file__).resolve().parent.parent))
server_commands = build_default_server_commands(rcap_project_root)

_tool_to_server = build_tool_to_server_map(registry)
_tool_executor = make_env_aware_stateless_executor(server_commands, _tool_to_server, extra_env=dict(_os.environ))

trust_lookup = QapPTrustLookup(orchestrator)
rcap_gateway = RCAPGateway(RcapConfig(data_dir=phase4_config.data_dir), registry, ledger, _tool_executor,
                          trust_lookup=trust_lookup)
consent_registry = ConsentRegistry(phase4_config.consent_db_path, ledger)
phase4_gateway = Phase4Gateway(rcap_gateway, registry, consent_registry, ledger)
top_trust_tier_gate = TopTrustTierGate(phase5_config, trust_lookup)
phase5_gateway = Phase5Gateway(phase4_gateway, top_trust_tier_gate, orchestrator, ledger)

from .broker.daily_reauth import DailyReauthManager

daily_reauth_manager = DailyReauthManager(
    config.daily_reauth_db_path,
    _os.environ.get("KITE_API_KEY", "unconfigured"), _os.environ.get("KITE_API_SECRET", "unconfigured"),
    cutover_hour_ist=config.daily_token_cutover_hour_ist, cutover_minute_ist=config.daily_token_cutover_minute_ist,
)
kite_credentials_configured = bool(_os.environ.get("KITE_API_KEY")) and bool(_os.environ.get("KITE_API_SECRET"))

ip_tracker = IPChangeTracker(config.ip_history_db_path, config.max_ip_changes_per_week)
live_trading_gate = LiveTradingGate(
    config,
    static_ip_gate=StaticIPHostGate(config),
    daily_reauth_gate=DailyReauthGate(daily_reauth_manager),
    rate_limiter=OrderRateLimiter(config.max_orders_per_second),
)
gateway = Phase6Gateway(phase5_gateway, top_trust_tier_gate, live_trading_gate, ledger)

app = FastAPI(title="Eternal Phase 6 — Algorithmic Trading")
app.mount("/qap", qap_app)


class ToolCallBody(BaseModel):
    session_id: Optional[str] = None
    tool_name: str
    arguments: Dict[str, Any] = {}
    confirmation_channel: str = "text_typed"
    caller_kind: str = "owner_interactive"
    source_label: str = "http_api"
    explanation: str
    rotate_token: Optional[str] = None


class KillSwitchBody(BaseModel):
    reason: str = "manual_trigger"


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "registered_tools": sorted(registry.keys()),
        "ledger_entries": ledger.count(),
        "ledger_source": __import__("trading_risk_engine.ledger_bridge", fromlist=["SOURCE"]).SOURCE,
        "live_trading_enabled": config.live_trading_enabled,
        "kite_credentials_configured": kite_credentials_configured,
    }


@app.post("/tools/call")
async def tools_call(body: ToolCallBody):
    request = WriteCallRequest(
        tool_name=body.tool_name, arguments=body.arguments, session_id=body.session_id,
        confirmation_channel=ConfirmationChannel(body.confirmation_channel),
        source_label=body.source_label, explanation=body.explanation,
        rotate_token=body.rotate_token, caller_kind=body.caller_kind,
    )
    result = await gateway.call_tool(request)
    return {
        "ok": result.ok, "content": result.content,
        "denied_reason": result.denied_reason, "ledger_entry_hashes": result.ledger_entry_hashes,
    }


@app.post("/tools/grant")
async def tools_grant(session_id: str, tool_name: str):
    try:
        token = gateway.grant_tool_permission(session_id, tool_name)
    except ValueError as e:
        return {"token": None, "error": str(e)}
    return {"token": token}


@app.post("/consent/grant")
async def consent_grant(tool_name: str, granted_by: str = "app_tap", note: str = ""):
    rec = consent_registry.grant(tool_name, granted_by, note)
    return {"tool_name": rec.tool_name, "granted": rec.granted, "granted_by": rec.granted_by}


@app.get("/trust/{session_id}/top-tier")
async def trust_top_tier(session_id: str):
    decision = top_trust_tier_gate.check(session_id)
    return {"granted": decision.granted, "reason": decision.reason, "trust_score": decision.trust_score}


@app.get("/trading/live-readiness")
async def trading_live_readiness():
    """One call answering 'could a live order even be attempted right
    now, ignoring session/trust entirely' — the regulatory half of the
    picture, useful for a phone app to show before a person tries."""
    from .models import OrderType
    result = live_trading_gate.check_before_order(OrderType.LIMIT, None)
    return {"allowed": result.allowed, "reason": result.reason, "live_trading_enabled": config.live_trading_enabled}


@app.post("/kill_switch")
async def kill_switch(body: KillSwitchBody):
    report = phase5_gateway.pull_kill_switch(reason=body.reason)
    return {
        "triggered_at": report.triggered_at, "reason": report.reason,
        "devices_seen": report.devices_seen, "sessions_quarantined": report.sessions_quarantined,
        "sessions_already_quarantined": report.sessions_already_quarantined,
        "vault_tokens_revoked": report.vault_tokens_revoked, "rotate_tokens_revoked": report.rotate_tokens_revoked,
        "consent_revoked_for_tools": report.consent_revoked_for_tools,
        "ledger_entry_hash": report.ledger_entry_hash,
    }


@app.get("/ledger")
async def ledger_recent(limit: int = 50):
    return ledger.recent(limit)


@app.get("/ledger/verify")
async def ledger_verify():
    intact, break_index = ledger.verify_chain()
    return {"intact": intact, "break_index": break_index}
