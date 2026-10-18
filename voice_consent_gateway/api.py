"""
Phase 4 HTTP daemon — same role as rcap_p/api.py (Phase 3) and
qap_p/api.py (Phase 1): what would actually run on the mini-PC/Pi,
reachable over the Tailscale mesh once Phase 5 exists. Binds only to
whatever interface it's told to (loopback here); no rate limiting/mTLS of
its own yet, same documented limitation Phase 1 and Phase 3 both carry
forward rather than pretend to have solved early.

Endpoints are additive to Phase 3's own daemon, not a replacement — a real
deployment would run this alongside (or merged into) rcap_p.api, sharing
the same underlying RCAPGateway instance so ROTATE tokens, ATTRIBUTE
history, and the ledger are consistent whether a call came in through
Phase 3's endpoints or Phase 4's.

Tool execution uses rcap_p.mcp_client.make_stateless_executor, not
MCPServerPool — same reason rcap_p/api.py itself uses it and not the
pooled version: ASGI gives each HTTP request its own asyncio Task, and a
pooled MCP connection's cancel scope belongs to whichever Task opened it.
"""
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from rcap_p.config import Config as RcapConfig
from rcap_p.gateway import RCAPGateway

from .config import Config
from .consent import ConsentRegistry
from .deployment import build_default_server_commands
from .gateway import Phase4Gateway
from .ledger_bridge import Ledger
from .memory.gated_writer import GatedMemoryWriter
from .memory.store import MemoryStore
from .models import ConfirmationChannel, MemoryCategory, WriteCallRequest
from .registry_ext import build_registry
from .voice.data_classification import classify as classify_outbound
from .voice.tts import ElevenLabsCloudTTS, LocalTTS, PolicyEnforcingTTS

from rcap_p.mcp_client import build_tool_to_server_map, make_stateless_executor

config = Config()
ledger = Ledger(config.ledger_db_path)
registry = build_registry()

# rcap_p's own project root (wherever rcap-p/ was unpacked/installed) is
# passed in via an env var so this daemon works whether Phase 3 lives
# alongside Phase 4 or in a completely separate directory tree.
import os as _os
rcap_project_root = Path(_os.environ.get("RCAP_P_PROJECT_ROOT", Path(__file__).resolve().parent.parent))
server_commands = build_default_server_commands(rcap_project_root)

# Stateless (fresh-connection-per-call) executor, not MCPServerPool —
# reusing rcap_p.api's own hard-won Phase 3 finding, not rediscovering it:
# a pooled/kept-alive MCP connection ties its cancel scope to whichever
# asyncio Task opened it, and ASGI gives each HTTP request its own Task.
# See make_stateless_executor's docstring for the full reasoning. Phase 4
# hit exactly this failure during development (a shutdown-time traceback
# from the pooled version under uvicorn) before switching to this.
_tool_to_server = build_tool_to_server_map(registry)
_tool_executor = make_stateless_executor(server_commands, _tool_to_server)

rcap_gateway = RCAPGateway(RcapConfig(data_dir=config.data_dir), registry, ledger, _tool_executor)
consent_registry = ConsentRegistry(config.consent_db_path, ledger)
gateway = Phase4Gateway(rcap_gateway, registry, consent_registry, ledger)
memory_store = MemoryStore(config.memory_db_path)
gated_memory = GatedMemoryWriter(rcap_gateway, memory_store, ledger)
tts = PolicyEnforcingTTS(LocalTTS(), ElevenLabsCloudTTS())

app = FastAPI(title="Eternal Phase 4 — Voice + Write-Capable Capabilities")


class ToolCallBody(BaseModel):
    session_id: Optional[str] = None
    tool_name: str
    arguments: Dict[str, Any] = {}
    confirmation_channel: str = "text_typed"
    caller_kind: str = "owner_interactive"
    source_label: str = "http_api"
    explanation: str
    rotate_token: Optional[str] = None


class ConsentBody(BaseModel):
    tool_name: str
    granted_by: str = "app_tap"
    note: str = ""


class MemoryWriteBody(BaseModel):
    key: str
    value: str
    category: str = "general"
    source: str = "http_api"


class MemoryApproveBody(BaseModel):
    request_id: str
    key: str
    category: str = "general"
    source: str = "http_api"


class SpeakBody(BaseModel):
    text: str
    domain_hint: Optional[str] = None
    requested_backend: str = "cloud"


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "registered_tools": sorted(registry.keys()),
        "ledger_entries": ledger.count(),
        "ledger_source": __import__("voice_consent_gateway.ledger_bridge", fromlist=["SOURCE"]).SOURCE,
    }


@app.post("/tools/call")
async def tools_call(body: ToolCallBody):
    request = WriteCallRequest(
        tool_name=body.tool_name,
        arguments=body.arguments,
        session_id=body.session_id,
        confirmation_channel=ConfirmationChannel(body.confirmation_channel),
        source_label=body.source_label,
        explanation=body.explanation,
        rotate_token=body.rotate_token,
        caller_kind=body.caller_kind,
    )
    result = await gateway.call_tool(request)
    return {
        "ok": result.ok,
        "content": result.content,
        "denied_reason": result.denied_reason,
        "ledger_entry_hashes": result.ledger_entry_hashes,
    }


@app.post("/tools/grant")
async def tools_grant(session_id: str, tool_name: str):
    token = rcap_gateway.grant_tool_permission(session_id, tool_name)
    return {"token": token}


@app.post("/consent/grant")
async def consent_grant(body: ConsentBody):
    rec = consent_registry.grant(body.tool_name, body.granted_by, body.note)
    return {"tool_name": rec.tool_name, "granted": rec.granted, "granted_by": rec.granted_by}


@app.post("/consent/revoke")
async def consent_revoke(tool_name: str, revoked_by: str = "app_tap"):
    consent_registry.revoke(tool_name, revoked_by)
    return {"tool_name": tool_name, "granted": False}


@app.get("/consent/{tool_name}")
async def consent_get(tool_name: str):
    rec = consent_registry.get(tool_name)
    if rec is None:
        return {"tool_name": tool_name, "granted": False, "note": "no consent record yet"}
    return {"tool_name": rec.tool_name, "granted": rec.granted, "granted_by": rec.granted_by, "granted_at": rec.granted_at}


@app.post("/memory/write")
async def memory_write(body: MemoryWriteBody):
    result = gated_memory.write_fact(body.key, body.value, MemoryCategory(body.category), body.source)
    return {
        "ok": result.ok,
        "containment_state": result.containment_state.value,
        "reasons": result.reasons,
        "approval_request_id": result.approval_request_id,
        "fact": None if result.fact is None else result.fact.__dict__,
    }


@app.post("/memory/approve")
async def memory_approve(body: MemoryApproveBody):
    result = gated_memory.approve_pending(body.request_id, body.key, MemoryCategory(body.category), body.source)
    return {"ok": result.ok, "fact": None if result.fact is None else result.fact.__dict__}


@app.get("/memory/facts")
async def memory_facts(category: Optional[str] = None):
    facts = memory_store.list_facts(MemoryCategory(category) if category else None)
    return [f.__dict__ for f in facts]


@app.post("/voice/classify")
async def voice_classify(body: SpeakBody):
    result = classify_outbound(body.text, domain_hint=MemoryCategory(body.domain_hint) if body.domain_hint else None)
    return {"data_class": result.data_class.value, "reasons": result.reasons}


@app.post("/voice/speak")
async def voice_speak(body: SpeakBody):
    result = tts.speak(body.text, domain_hint=MemoryCategory(body.domain_hint) if body.domain_hint else None,
                        requested_backend=body.requested_backend)
    return {
        "backend_used": result.backend_used, "data_class": result.data_class.value,
        "reasons": result.reasons, "audio_bytes_len": len(result.audio_bytes),
    }


# --- read-through to RCAP-P's own CONTAIN/ATTRIBUTE/ledger state, so a
# Phase 4 deployment doesn't need Phase 3's daemon running separately just
# to inspect what its own shared RCAPGateway instance is doing ---
@app.get("/contain/pending")
async def contain_pending():
    return [
        {"request_id": r.request_id, "tool_name": r.tool_name, "source_label": r.source_label, "reasons": r.reasons}
        for r in rcap_gateway.contain_queue.pending()
    ]


@app.post("/contain/{request_id}/approve")
async def contain_approve(request_id: str):
    req = rcap_gateway.contain_queue.approve(request_id)
    return {"request_id": req.request_id, "approved": req.approved}


@app.post("/contain/{request_id}/reject")
async def contain_reject(request_id: str):
    req = rcap_gateway.contain_queue.reject(request_id)
    return {"request_id": req.request_id, "approved": req.approved}


@app.get("/ledger")
async def get_ledger(limit: int = 50):
    return ledger.recent(limit)


@app.get("/ledger/verify")
async def verify_ledger():
    ok, bad_id = ledger.verify_chain()
    return {"intact": ok, "first_broken_entry_id": bad_id}
