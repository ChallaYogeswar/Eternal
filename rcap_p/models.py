"""
Core data models for RCAP-P (personal-scale Rotate-Contain-Attribute Protocol).

Maps onto RCAP_Whitepaper_CHALLAYOGESWAR.docx Section 3.1 (Ring Roles) and
Eternal_Master_Build_Plan.md Section 6.2's personal-scale table:

    RCAP ring   Personal-scale role
    ----------  --------------------------------------------------------
    PERIMETER   Rate-limit any externally-reachable surface
    INSPECT     Gates every MCP tool response, not just fetched web pages
    ROTATE      Scheduled credential rotation + session-scoped tool perms
    DECEIVE     Decoy secret / fake tool registration
    CORE        Real vault; default tool access only through mediation
    FASTLANE    You, via passkey, always frictionless
    CONTAIN     Sandboxed/dry-run + one manual approval, not a hard crash
    ATTRIBUTE   Tags which source triggered a flag; repeat offenders auto-contained

Same pattern as qap_p/models.py in Phase 1: plain dataclasses/enums, no
behavior, so every other module can share one vocabulary.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class CallerKind(str, Enum):
    """Whitepaper Section 3.5 / build plan Section 4: FASTLANE is you, via
    passkey — not "code you personally wrote." An automated routine is not
    exempt just because its author is trusted.

    Build plan, verbatim: "Every automated routine — including ones you
    wrote yourself — goes through the full ring stack, no exceptions."

    This is the field that operationalizes that sentence: FASTLANE eligibility
    (fastlane.py) checks CallerKind first, before it ever looks at trust score.
    """
    OWNER_INTERACTIVE = "owner_interactive"   # a live turn, you're at the keyboard/mic right now
    AUTOMATED_ROUTINE = "automated_routine"   # a schedule, a proactive trigger, an agentic chain step


class InspectVerdict(str, Enum):
    """Ring 2 (INSPECT) outcome for one piece of content. Whitepaper Section
    3.1: 'tags sessions for containment versus pass-through.' Build plan
    Section 6.2: 'Flags, never silently strips.'"""
    CLEAN = "clean"                 # no injection-shaped signal found
    SUSPICIOUS = "suspicious"       # ambiguous-but-not-certain -> CONTAIN (soft)
    MALICIOUS = "malicious"         # high-confidence signal -> CONTAIN (hard) / block


class ContainmentState(str, Enum):
    """RCAP whitepaper Section 3.3 (CONTAIN): 'CONTAIN is not a block.' A
    session/content that is admitted but not yet trusted has its egress
    closed and is resource-throttled rather than dropped. Mapped to personal
    scale (My_Verdict_on_Outline.md): 'Ambiguous-but-not-certain flags get
    sandboxed/dry-run + one manual approval, not a hard crash.'"""
    PASSTHROUGH = "passthrough"     # not contained; delivered to context normally
    CONTAINED = "contained"         # sandboxed, held for one manual approval
    HARD_BLOCKED = "hard_blocked"   # exhausted max duration or confirmed-malicious; not delivered


class ToolSensitivity(str, Enum):
    """Not every tool deserves the same friction. Phase 3 only registers
    read-only tools (build plan Phase 3 scope), so nothing here reaches
    SENSITIVE yet — but the field exists now so Phase 4/5's write-capable
    and physical-action tools have somewhere to plug in without a registry
    schema change."""
    READ_ONLY = "read_only"
    WRITE = "write"
    SENSITIVE = "sensitive"


@dataclass
class ToolManifestEntry:
    """One row in the capability registry (build plan Section 4: 'New tools
    onboard via: write a small manifest -> wrap as MCP server -> register ->
    vault issues a scoped token'). This is the manifest."""
    tool_name: str
    server_key: str                 # which mcp_servers/<server_key> this belongs to
    description: str
    sensitivity: ToolSensitivity
    is_decoy: bool = False           # DECEIVE ring: a registered tool that should never really be called
    output_schema: Optional[type] = None  # a Pydantic model class, for INSPECT's structured-data path
    rate_limit_per_minute: int = 30  # PERIMETER


@dataclass
class ToolCallContext:
    """Everything the ring pipeline needs to know about *who* is asking,
    independent of *what* they're asking for. One of these is built per
    call, from the QAP-P session (Phase 1) if available."""
    session_id: Optional[str]
    caller_kind: CallerKind
    source_label: str = "unspecified"   # ATTRIBUTE: which site/sender/doc/server this call is chained from


@dataclass
class AttemptRecord:
    provider: str
    ok: bool
    detail: str = ""


@dataclass
class InspectFinding:
    verdict: InspectVerdict
    reasons: List[str] = field(default_factory=list)
    matched_patterns: List[str] = field(default_factory=list)


@dataclass
class ToolCallResult:
    """What the gateway hands back for one tool call, whichever path it
    took. `content` is always safe to show/feed to a downstream LLM —
    flagged content is replaced with a clear placeholder, never passed
    through raw and never allowed to raise an uncaught exception up to the
    caller (build plan Phase 3 exit criterion: 'not silently passed or
    hard-crashed')."""
    tool_name: str
    ok: bool
    content: Any
    containment_state: ContainmentState
    fastlane: bool
    inspect_finding: Optional[InspectFinding]
    attributed_source: Optional[str]
    ledger_entry_hashes: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ApprovalRequest:
    """A CONTAINED result waiting on the one manual approval the whitepaper
    describes. Phase 3 has no phone-app UI to tap "approve" on, so this is
    surfaced as a queryable queue (see contain.py) — the real approval
    surface is a Phase 4+ UI concern, not a Phase 3 gap."""
    request_id: str
    tool_name: str
    source_label: str
    reasons: List[str]
    raw_content: Any
    created_at: float
    resolved: bool = False
    approved: Optional[bool] = None
