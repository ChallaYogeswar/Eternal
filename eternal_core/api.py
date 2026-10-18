"""
Network-facing API — what actually runs on the mini-PC and is reachable
over Tailscale.

Eternal_Master_Build_Plan.md Phase 2 exit criteria: "you can text Eternal
from your phone on any network and get a grounded answer referencing your
own documents, with zero write access to anything." POST /chat is that
surface: a phone app (or, in this build environment, a plain HTTP client —
see README, "What's simulated vs real") sends text, gets text back.

Deliberately absent from this API, and why:
  - No /ingest or any endpoint that (re)builds the document index remotely.
    Adding to what Eternal knows is a write-shaped action; see
    scripts/ingest_docs.py's docstring for the full reasoning (short
    version: that belongs behind RCAP-P's INSPECT gate, Phase 3+, not
    exposed bare over the network in Phase 2).
  - No endpoint that calls out to ÆTHER, CinemaVault, Home Assistant, or
    any tracker — Phase 2 has no client code for any of them at all.
  - No endpoint that writes to the filesystem outside Eternal's own data
    dir, or executes anything.
The only write this whole service can ever perform is its own ledger
entry per chat turn (see graph/nodes.py's finish node) — recorded, never
exposed for remote deletion or modification via this API either (GET only
on /ledger).
"""
import time
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import Config
from .assistant import Assistant, IndexNotBuiltError

app = FastAPI(title="Eternal — Phase 2 (Text-Only Assistant Loop)")

_config = Config()
_assistant: Optional[Assistant] = None
_startup_error: Optional[str] = None

try:
    _assistant = Assistant(_config)
except IndexNotBuiltError as e:
    # Don't crash the process on startup — /health should be able to report
    # "index not built yet" rather than the daemon refusing to even boot,
    # which would be a worse operator experience on a headless mini-PC.
    _startup_error = str(e)


class ChatRequest(BaseModel):
    session_id: str
    message: str


class SourceInfo(BaseModel):
    title: str
    section: str
    distance: float


class ChatResponse(BaseModel):
    response: str
    provider_used: str
    degraded: bool
    sources: List[SourceInfo]
    latency_seconds: float


def _require_assistant() -> Assistant:
    if _assistant is None:
        raise HTTPException(
            status_code=503,
            detail=f"Eternal's document index isn't built yet. {_startup_error}",
        )
    return _assistant


@app.get("/healthz")
def healthz():
    if _assistant is None:
        return {"status": "not_ready", "reason": _startup_error}
    health = _assistant.health()
    return {"status": "ok", **health}


@app.get("/sources")
def sources():
    """Lists what Eternal actually has indexed right now — lets you sanity
    check grounding claims instead of taking them on faith."""
    assistant = _require_assistant()
    import os

    docs_dir = assistant.config.docs_dir
    if not os.path.isdir(docs_dir):
        return {"docs_dir": docs_dir, "files": []}
    files = sorted(f for f in os.listdir(docs_dir) if f.lower().endswith((".md", ".txt", ".docx")))
    return {"docs_dir": docs_dir, "files": files}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    assistant = _require_assistant()
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")

    start = time.time()
    result = assistant.chat(req.session_id, req.message)
    latency = time.time() - start

    sources = [
        SourceInfo(title=c["title"], section=c["section"], distance=c["distance"])
        for c in result["retrieved_chunks"]
    ]
    return ChatResponse(
        response=result["response"],
        provider_used=result["provider_used"],
        degraded=result["degraded"],
        sources=sources,
        latency_seconds=latency,
    )


@app.post("/chat/reset")
def chat_reset(session_id: str):
    assistant = _require_assistant()
    assistant.reset_session(session_id)
    return {"status": "ok", "session_id": session_id}


@app.get("/ledger")
def ledger(limit: int = 50):
    """Read-only. Mirrors qap_p's /ledger endpoint in spirit — every
    external request and chat turn this phase has ever made, in order."""
    assistant = _require_assistant()
    return {"entries": assistant.ledger.recent(limit)}


@app.get("/ledger/verify")
def ledger_verify():
    assistant = _require_assistant()
    ok, broken_at = assistant.ledger.verify_chain()
    return {"chain_intact": ok, "first_broken_entry_id": broken_at}
