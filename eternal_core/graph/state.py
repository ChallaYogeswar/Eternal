"""
State shape for Eternal's Phase 2 reasoning loop.

Deliberately flat and fully serializable (plain str/float/bool/list/dict) —
nothing in here is a live object handle (no provider instances, no DB
connections), so the whole state can be logged to the ledger or returned
from the API without any special-casing.
"""
from typing import TypedDict, List, Dict, Optional


class EternalState(TypedDict):
    session_id: str
    user_message: str
    history: List[Dict[str, str]]          # prior turns this session, in-memory only (see api.py)

    retrieved_chunks: List[Dict[str, str]]  # [{"text":.., "source":.., "title":.., "section":.., "distance":..}]

    response: Optional[str]
    provider_used: Optional[str]            # "ollama:<model>" | "anthropic:<model>" | "openai:<model>" | "retrieval_only"
    degraded: bool                          # True once no LLM provider succeeded
    attempts: List[Dict[str, str]]          # every provider tried, ok/error, for audit + /health
    tool_call: Optional[Dict[str, object]] # structured request from an intent/tool router
    tool_result: Optional[Dict[str, object]]
    started_at: float
