"""
Phase 5 HTTP daemon — additive to Phase 4's, same pattern Phase 4 used
relative to Phase 3: a real deployment runs ONE daemon (this one) that
composes everything below it, rather than N daemons per phase.

New in this daemon, closing an item Phase 3's own README left open
("Wire a real qap_p.orchestrator.Orchestrator-backed TrustLookup into
RCAPGateway(..., trust_lookup=...) so FASTLANE actually evaluates real
sessions instead of NullTrustLookup's always-False default" — checked
directly: neither rcap_p/api.py nor voice_consent_gateway/api.py did this): this daemon
mounts qap_p.api.app at /qap (Phase 1's own device-enrollment/session
endpoints, completely unmodified) and constructs RCAPGateway with
trust_lookup=QapPTrustLookup(orchestrator) — the SAME orchestrator object
the mounted /qap/* routes use, so a device enrolled and a session created
through /qap/devices/enroll + /qap/sessions is the same session Phase 5's
own top-trust-tier gate (and, from now on, RCAP-P's own FASTLANE) will
evaluate. Before this, FASTLANE existed as code but had never been
connected to anything real in this project.

Binds only to whatever interface it's told to (loopback here); no rate
limiting/mTLS of its own yet — same documented limitation every earlier
phase's daemon carries forward rather than pretending to have solved.
"""
import os as _os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from api_auth.approvals import ApprovalStore
from api_auth.fastapi_deps import require
from api_auth.principals import Principal
from api_auth.tool_call import authorize_tool_call, build_approval_router
from voice_consent_gateway.config import Config as Phase4Config
from voice_consent_gateway.consent import ConsentRegistry
from voice_consent_gateway.gateway import Phase4Gateway
from voice_consent_gateway.models import ConfirmationChannel, WriteCallRequest

from qap_p.api import app as qap_app
from qap_p.api import orchestrator
from rcap_p.config import Config as RcapConfig
from rcap_p.gateway import RCAPGateway
from rcap_p.mcp_client import build_tool_to_server_map

from .config import Config
from .deployment import build_default_server_commands
from .gateway import Phase5Gateway
from .ledger_bridge import Ledger
from .mcp_client_env import make_env_aware_stateless_executor
from .registry_ext import build_registry
from .trust_bridge import QapPTrustLookup
from .trust_tier import TopTrustTierGate

config = Config()
phase4_config = Phase4Config(data_dir=config.data_dir)
ledger = Ledger(config.ledger_db_path)
registry = build_registry()

rcap_project_root = Path(_os.environ.get("RCAP_P_PROJECT_ROOT", Path(__file__).resolve().parent.parent))
server_commands = build_default_server_commands(rcap_project_root)

_tool_to_server = build_tool_to_server_map(registry)
# make_env_aware_stateless_executor, not rcap_p.mcp_client's own
# make_stateless_executor — see device_control_gateway/mcp_client_env.py's module
# docstring for the real, verified gap this closes: without it,
# HOME_ASSISTANT_BASE_URL/HOME_ASSISTANT_TOKEN (and every other
# subprocess-facing env var, including Phase 3/4's own AETHER_DB_PATH-
# style config) would be silently unreachable by any subprocess-spawned
# MCP server, no matter how this daemon process itself is configured.
_tool_executor = make_env_aware_stateless_executor(server_commands, _tool_to_server, extra_env=dict(_os.environ))

# --- the fix: a real trust_lookup, backed by the SAME orchestrator the
# mounted /qap/* routes below operate on ---
trust_lookup = QapPTrustLookup(orchestrator)
rcap_gateway = RCAPGateway(RcapConfig(data_dir=phase4_config.data_dir), registry, ledger, _tool_executor,
                            trust_lookup=trust_lookup)
consent_registry = ConsentRegistry(phase4_config.consent_db_path, ledger)
phase4_gateway = Phase4Gateway(rcap_gateway, registry, consent_registry, ledger)
top_trust_tier_gate = TopTrustTierGate(config, trust_lookup)
gateway = Phase5Gateway(phase4_gateway, top_trust_tier_gate, orchestrator, ledger)

app = FastAPI(title="Eternal Phase 5 — Remote Device Control + Home Assistant")
app.mount("/qap", qap_app)
_approvals = ApprovalStore()
app.include_router(build_approval_router(_approvals, registry))


class ToolCallBody(BaseModel):
    session_id: Optional[str] = None
    tool_name: str
    arguments: Dict[str, Any] = {}
    confirmation_channel: Optional[str] = None   # ignored unless the authenticated principal holds it (audit E-01)
    caller_kind: Optional[str] = None            # ignored unless it matches the principal
    approval_id: Optional[str] = None            # required for SENSITIVE tools
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
        "integration_modes": __import__("mcp_servers.integration_status", fromlist=["integration_modes"]).integration_modes(),
        "auth_configured": __import__("api_auth.fastapi_deps", fromlist=["get_registry"]).get_registry().configured,
        "ledger_entries": ledger.count(),
        "ledger_source": __import__("device_control_gateway.ledger_bridge", fromlist=["SOURCE"]).SOURCE,
        "fastlane_trust_lookup": "qap_p.orchestrator.Orchestrator (real, via QapPTrustLookup)",
    }


@app.post("/tools/call")
async def tools_call(body: ToolCallBody, principal: Principal = Depends(require("tools:call"))):
    channel, caller_kind = authorize_tool_call(
        principal, approvals=_approvals, registry=registry, tool_name=body.tool_name, session_id=body.session_id,
        arguments=body.arguments, requested_channel=body.confirmation_channel,
        requested_caller_kind=body.caller_kind, approval_id=body.approval_id)
    request = WriteCallRequest(
        tool_name=body.tool_name,
        arguments=body.arguments,
        session_id=body.session_id,
        confirmation_channel=ConfirmationChannel(channel),
        source_label=body.source_label,
        explanation=body.explanation,
        rotate_token=body.rotate_token,
        caller_kind=caller_kind,
    )
    result = await gateway.call_tool(request)
    return {
        "ok": result.ok,
        "content": result.content,
        "denied_reason": result.denied_reason,
        "ledger_entry_hashes": result.ledger_entry_hashes,
    }


@app.post("/tools/grant")
async def tools_grant(session_id: str, tool_name: str, principal: Principal = Depends(require("tools:grant"))):
    # Guarded by QAP-P session state (gateway.py) — NOT calling
    # rcap_gateway.grant_tool_permission directly, so a session the kill
    # switch already quarantined cannot be handed a fresh ROTATE token.
    # See device_control_gateway/gateway.py's module docstring for why this guard exists.
    try:
        token = gateway.grant_tool_permission(session_id, tool_name)
    except ValueError as e:
        return {"token": None, "error": str(e)}
    return {"token": token}


@app.post("/consent/grant")
async def consent_grant(tool_name: str, note: str = "", principal: Principal = Depends(require("consent:write"))):
    # Audit E-01: `granted_by` is the authenticated principal, no longer a caller-supplied string.
    rec = consent_registry.grant(tool_name, f"{principal.id}@{principal.default_channel}", note)
    return {"tool_name": rec.tool_name, "granted": rec.granted, "granted_by": rec.granted_by}


@app.get("/trust/{session_id}/top-tier", dependencies=[Depends(require("qap:read"))])
async def trust_top_tier(session_id: str):
    """Read-only check against the SAME gate remote_control_request_full_
    session must clear, useful for a phone app to show 'you're not
    eligible for full control yet, here's why' before the person even
    tries."""
    decision = top_trust_tier_gate.check(session_id)
    return {"granted": decision.granted, "reason": decision.reason, "trust_score": decision.trust_score}


@app.post("/kill_switch", dependencies=[Depends(require("kill:trigger"))])
async def kill_switch(body: KillSwitchBody):
    report = gateway.pull_kill_switch(reason=body.reason)
    return {
        "triggered_at": report.triggered_at,
        "reason": report.reason,
        "devices_seen": report.devices_seen,
        "sessions_quarantined": report.sessions_quarantined,
        "sessions_already_quarantined": report.sessions_already_quarantined,
        "vault_tokens_revoked": report.vault_tokens_revoked,
        "rotate_tokens_revoked": report.rotate_tokens_revoked,
        "consent_revoked_for_tools": report.consent_revoked_for_tools,
        "ledger_entry_hash": report.ledger_entry_hash,
    }


@app.get("/ledger", dependencies=[Depends(require("ledger:read"))])
async def get_ledger(limit: int = 50):
    return ledger.recent(limit)


@app.get("/ledger/verify", dependencies=[Depends(require("ledger:read"))])
async def verify_ledger():
    ok, bad_id = ledger.verify_chain()
    return {"intact": ok, "first_broken_entry_id": bad_id}
