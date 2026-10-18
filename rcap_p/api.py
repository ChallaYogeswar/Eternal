"""
RCAP-P daemon — the network-facing surface, same role
qap_p/api.py plays for Phase 1 and eternal_core/api.py plays for Phase 2.

Not the primary way Eternal's own LangGraph orchestrator would reach
RCAP-P in an integrated deployment (that's a direct Python import of
RCAPGateway, same process) — this exists for exactly the same reason
Phase 1's daemon does: a phone app needs an HTTP surface to tap "approve"
on a CONTAINED item, and any deployment topology where RCAP-P runs as its
own service (rather than in-process with Eternal Core) needs a wire
protocol. Binds to loopback only here, same "don't expose this publicly,
the Tailscale interface is the real deployment boundary" posture Phase 1's
README already establishes for qap_p/api.py.
"""
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # rcap_p/api.py -> up twice to the project root
sys.path.insert(0, str(PROJECT_ROOT))

from rcap_p.config import Config
from rcap_p.deployment import build_default_server_commands
from rcap_p.gateway import RCAPGateway
from rcap_p.ledger_bridge import Ledger
from rcap_p.mcp_client import build_tool_to_server_map, make_stateless_executor
from rcap_p.models import CallerKind, ToolCallContext
from rcap_p.registry import build_default_registry

app = FastAPI(title="RCAP-P", description="Rotate-Contain-Attribute Protocol, personal scale (Phase 3)")

_config = Config()
_ledger = Ledger(_config.ledger_db_path)
_registry = build_default_registry()
# Stateless (fresh-connection-per-call) executor — see mcp_client.py's
# make_stateless_executor docstring for why the pooled/kept-alive
# executor is unsafe here specifically (ASGI runs each request in its own
# asyncio Task; anyio's stdio transport ties a connection's cancel scope
# to whichever Task opened it).
_executor = make_stateless_executor(
    build_default_server_commands(PROJECT_ROOT), build_tool_to_server_map(_registry)
)
_gateway = RCAPGateway(config=_config, registry=_registry, ledger=_ledger, tool_executor=_executor)


class CallToolRequest(BaseModel):
    session_id: str
    tool_name: str
    arguments: Dict[str, Any] = {}
    rotate_token: Optional[str] = None
    caller_kind: str = "owner_interactive"  # or "automated_routine"
    source_label: str = "unspecified"


class GrantRequest(BaseModel):
    session_id: str
    tool_name: str


@app.get("/healthz")
def healthz():
    intact, _ = _ledger.verify_chain()
    return {
        "status": "ok",
        "registered_tools": sorted(_registry.keys()),
        "ledger_entries": _ledger.count(),
        "ledger_chain_intact": intact,
    }


@app.post("/tools/call")
async def call_tool(req: CallToolRequest):
    try:
        caller_kind = CallerKind(req.caller_kind)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid caller_kind '{req.caller_kind}'")

    ctx = ToolCallContext(session_id=req.session_id, caller_kind=caller_kind, source_label=req.source_label)
    result = await _gateway.call_tool(ctx, req.tool_name, req.arguments, rotate_token=req.rotate_token)
    return {
        "tool_name": result.tool_name,
        "ok": result.ok,
        "content": result.content,
        "containment_state": result.containment_state.value,
        "fastlane": result.fastlane,
        "attributed_source": result.attributed_source,
        "error": result.error,
    }


@app.post("/tools/grant")
def grant_permission(req: GrantRequest):
    if req.tool_name not in _registry:
        raise HTTPException(status_code=404, detail=f"unknown tool '{req.tool_name}'")
    token = _gateway.grant_tool_permission(req.session_id, req.tool_name)
    return {"token": token, "ttl_seconds": _config.rotate_token_ttl_seconds}


@app.get("/contain/pending")
def contain_pending():
    return [
        {
            "request_id": r.request_id, "tool_name": r.tool_name, "source_label": r.source_label,
            "reasons": r.reasons, "created_at": r.created_at,
        }
        for r in _gateway.contain_queue.pending()
    ]


@app.post("/contain/{request_id}/approve")
def contain_approve(request_id: str):
    try:
        req = _gateway.contain_queue.approve(request_id)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"request_id": req.request_id, "approved": req.approved}


@app.post("/contain/{request_id}/reject")
def contain_reject(request_id: str):
    try:
        req = _gateway.contain_queue.reject(request_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"request_id": req.request_id, "approved": req.approved}


@app.get("/attribute/report")
def attribute_report():
    return [
        {
            "source_label": r.source_label, "flag_count": r.flag_count,
            "total_calls": r.total_calls, "hard_blocked": r.hard_blocked,
        }
        for r in _gateway.attribute.report()
    ]


@app.get("/ledger")
def ledger_recent(limit: int = 100):
    return _ledger.recent(limit=limit)


@app.get("/ledger/verify")
def ledger_verify():
    intact, broken_id = _ledger.verify_chain()
    return {"intact": intact, "first_broken_entry_id": broken_id}
