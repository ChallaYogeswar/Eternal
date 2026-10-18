"""
Core data models for Phase 4 (Voice + Write-Capable Capabilities).

Same posture as rcap_p/models.py in Phase 3: plain dataclasses/enums, no
behavior, so every other module shares one vocabulary. Phase 4 does not
modify rcap_p.models — it composes with it. A Phase 4 tool call ultimately
becomes an rcap_p.models.ToolCallContext + a call into RCAPGateway.call_tool;
what's here is the extra vocabulary Phase 4 needs *before* that point:
who's confirming the action, and through what channel.

Eternal_Master_Build_Plan.md Section 6.3, point 2 (verbatim): "Sensitive-
tier actions (trade execution, full laptop control, physical Home
Assistant actions like locks) are never voice-triggerable alone, regardless
of trust score. Require a second, non-voice confirmation — a tap in the
phone app, not a spoken 'yes.'" ConfirmationChannel is the field that
operationalizes "voice" vs. "not voice" as a first-class, checkable value
instead of an implicit assumption buried in caller code.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ConfirmationChannel(str, Enum):
    """How this particular call is being authorized right now — orthogonal
    to CallerKind (rcap_p.models) and to QAP-P trust score (Phase 1).
    A session can be ACTIVE, hardware-backed, and high-trust, and the
    request can *still* have arrived over VOICE — trust score answers
    "is this the owner's device," not "was this specific instruction
    spoken or tapped." Both questions matter; they are not the same
    question. See voice_gate.py for the rule this field exists to enforce.
    """
    VOICE = "voice"            # via the wake-word/STT pipeline (voice/pipeline.py) — untrusted for SENSITIVE tier
    TEXT_TYPED = "text_typed"  # typed in the chat/app interface
    APP_TAP = "app_tap"        # explicit tap/button in the phone app — the "non-voice confirmation" the build plan calls for
    AUTOMATED = "automated"    # a scheduled/proactive routine; no live human channel at all


class MemoryCategory(str, Enum):
    """Eternal_Master_Build_Plan.md Section 6.3, point 3's outbound
    data-classification categories, reused here as the *memory* domain tag
    (see voice/data_classification.py for the outbound-TTS/LLM use of the
    same categories). A structured fact's category decides both (a)
    whether it's ever spoken back over cloud TTS and (b) nothing about
    whether it gets INSPECT-gated — every write is INSPECT-gated,
    regardless of category (Section 6.3 point 4: "No exception for 'it's
    just going into memory.'")."""
    GENERAL = "general"
    FINANCE = "finance"
    HEALTH = "health"
    DREAM_JOURNAL = "dream_journal"
    RELATIONSHIP = "relationship"


@dataclass
class VoiceTurnContext:
    """Everything one spoken interaction produced, before it's turned into
    a tool call. Built by voice/pipeline.py; consumed by gateway.py and by
    intent.py. `speaker_match`/`speaker_score` are informational only —
    see voice/speaker_verify.py's module docstring for why they are never
    treated as authorization."""
    session_id: Optional[str]
    raw_transcript: str
    transcript_confidence: float
    wake_word_triggered: bool
    speaker_match: Optional[bool]
    speaker_score: Optional[float]
    language: str = "en"


@dataclass
class ConsentRecord:
    tool_name: str
    granted: bool
    granted_by: str            # e.g. "owner_interactive:sess1" or "app_tap"
    granted_at: float
    revoked_at: Optional[float] = None
    note: str = ""


@dataclass
class WriteCallRequest:
    """What a caller (voice pipeline, text chat, or a future LangGraph tool
    node) hands to Phase4Gateway.call_tool. `explanation` is not optional
    padding — it is the literal mechanism behind the Phase 4 exit
    criterion's "is logged with an explanation" clause: gateway.py writes
    it into the ledger entry for this call, in the caller's own words, not
    a paraphrase reconstructed after the fact."""
    tool_name: str
    arguments: Dict[str, Any]
    session_id: Optional[str]
    confirmation_channel: ConfirmationChannel
    source_label: str
    explanation: str
    rotate_token: Optional[str] = None
    caller_kind: str = "owner_interactive"  # rcap_p.models.CallerKind value, kept as str to avoid a hard rcap_p dependency here


@dataclass
class WriteCallResult:
    tool_name: str
    ok: bool
    content: Any
    denied_reason: Optional[str]     # set only when Phase 4's OWN gates (voice-gate, consent) denied the call
    underlying_result: Optional[Any] # the rcap_p.models.ToolCallResult, when the call reached RCAP-P at all
    ledger_entry_hashes: List[str] = field(default_factory=list)
