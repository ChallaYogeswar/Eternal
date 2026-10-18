"""
Core contracts for the orchestration layer (plan v2.1, section 3).

Everything here is plain, frozen/serializable data in the same flat-state style as
`eternal_core.graph.state.EternalState`, so it can be written to the run ledger or returned from an
API without special-casing. Nothing in this module imports a gateway, an MCP client, or an LLM
provider.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple, Type

from pydantic import BaseModel, field_validator


class Provenance(str, Enum):
    USER_TYPED = "user_typed"
    USER_VOICE = "user_voice"
    RAG_INTERNAL = "rag_internal"
    WEB = "web"
    TOOL_OUTPUT = "tool_output"
    AGENT_OUTPUT = "agent_output"


# rag_internal is untrusted too: indexed documents can contain injected text.
UNTRUSTED_PROVENANCE: FrozenSet[Provenance] = frozenset(
    {Provenance.WEB, Provenance.TOOL_OUTPUT, Provenance.AGENT_OUTPUT, Provenance.RAG_INTERNAL}
)


class Sensitivity(IntEnum):
    PUBLIC = 0
    INTERNAL = 1
    SECRET = 2


class ToolTier(IntEnum):
    READ_ONLY = 0
    WRITE = 1
    SENSITIVE = 2


class ModelPolicy(str, Enum):
    """Upper bound only: the router still checks the live context labels on every call."""
    LOCAL_ONLY = "local_only"
    CLOUD_ALLOWED = "cloud_allowed"


class RunStatus(str, Enum):
    OK = "ok"
    REFUSED = "refused"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DENIED_OUT = "denied_out"
    FAILED = "failed"
    KILLED = "killed"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


SEVERITY_RANK: Dict[Severity, int] = {s: i for i, s in enumerate(Severity)}


class FindingOrigin(str, Enum):
    CHECK = "check"   # produced by a deterministic check; severity cannot be lowered by the model
    MODEL = "model"


@dataclass(frozen=True)
class Labels:
    """Attached to every datum entering a run's context. Labels never decrease: `join` takes the
    max sensitivity, the union of provenance, and the OR of the PII flag."""
    sensitivity: Sensitivity = Sensitivity.PUBLIC
    provenance: FrozenSet[Provenance] = frozenset()
    pii: bool = False

    def join(self, other: "Labels") -> "Labels":
        return Labels(
            sensitivity=Sensitivity(max(self.sensitivity, other.sensitivity)),
            provenance=self.provenance | other.provenance,
            pii=self.pii or other.pii,
        )

    @property
    def untrusted(self) -> bool:
        return bool(self.provenance & UNTRUSTED_PROVENANCE)

    def to_dict(self) -> Dict[str, Any]:
        return {"sensitivity": self.sensitivity.name.lower(),
                "provenance": sorted(p.value for p in self.provenance), "pii": self.pii}


@dataclass(frozen=True)
class Budget:
    max_steps: int = 12
    max_tool_calls: int = 20
    max_tokens: int = 24_000          # estimated (len/4) — providers do not report token counts
    wall_clock_s: float = 120.0
    per_call_timeout_s: float = 30.0  # model and tool
    max_denials: int = 3
    max_repeat_calls: int = 2         # identical tool+args
    max_repairs: int = 2              # malformed-JSON / schema repair retries per run
    max_observation_chars: int = 4_000
    max_arg_bytes: int = 8_192


@dataclass(frozen=True)
class ToolInfo:
    """One catalog row the broker needs. `output_provenance` and `output_sensitivity` are REQUIRED
    positional fields on purpose: a tool registered without declaring them is a registration error
    (plan s12 risk: label discipline depends on every tool declaring labels)."""
    name: str
    tier: ToolTier
    output_provenance: Provenance
    output_sensitivity: Sensitivity
    output_pii: bool = False
    args_model: Optional[Type[BaseModel]] = None
    is_check: bool = False
    check_severity: Severity = Severity.INFO
    # (arguments, content) -> list of human-checkable source refs, e.g. ["file:a.py:10-40"] or URLs
    source_extractor: Optional[Callable[[Dict[str, Any], Any], List[str]]] = None


class SpecError(ValueError):
    pass


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


@dataclass(frozen=True)
class AgentSpec:
    name: str
    purpose: str
    system_prompt: str
    allowed_tools: FrozenSet[str]
    max_tool_tier: ToolTier
    max_data_sensitivity: Sensitivity
    output_schema: Type[BaseModel]
    budget: Budget = Budget()
    model_policy: ModelPolicy = ModelPolicy.LOCAL_ONLY
    can_write: bool = False

    def __post_init__(self):
        if not _NAME_RE.match(self.name):
            raise SpecError(f"agent name '{self.name}' must match {_NAME_RE.pattern}")
        if self.max_tool_tier >= ToolTier.SENSITIVE:
            raise SpecError("no agent spec may reach SENSITIVE-tier tools in v1 (invariant I15)")
        if not self.can_write and self.max_tool_tier > ToolTier.READ_ONLY:
            raise SpecError("can_write=False requires max_tool_tier=READ_ONLY")

    @property
    def spec_hash(self) -> str:
        canonical = json.dumps({
            "name": self.name, "purpose": self.purpose, "system_prompt": self.system_prompt,
            "allowed_tools": sorted(self.allowed_tools), "max_tool_tier": int(self.max_tool_tier),
            "max_data_sensitivity": int(self.max_data_sensitivity),
            "output_schema": self.output_schema.model_json_schema(),
            "budget": self.budget.__dict__, "model_policy": self.model_policy.value,
            "can_write": self.can_write,
        }, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class Task:
    task_id: str
    run_id: str
    goal: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    labels: Labels = Labels()
    origin_channel: str = "typed"       # typed | voice | api
    parent_task_id: Optional[str] = None
    depth: int = 0


class Finding(BaseModel):
    severity: Severity
    category: str
    claim: str
    evidence_refs: List[str]
    suggested_action: str = ""
    origin: FindingOrigin = FindingOrigin.MODEL

    @field_validator("claim", "category")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v


@dataclass
class StepRecord:
    index: int
    kind: str                         # tool | final | repair | model_error | denied | terminal
    claimed_reason: str = ""          # the model's stated reason — logged, never trusted
    tool: Optional[str] = None
    args_preview: str = ""
    verdict: str = ""
    decoy: bool = False
    observation_preview: str = ""
    labels: Optional[Dict[str, Any]] = None
    tokens_est: int = 0
    model_tier: str = ""            # local | cloud (cloud = external_request)
    provider: str = ""              # which provider answered (audit: where did the context go)
    latency_s: float = 0.0
    spec_hash: str = ""


@dataclass
class AgentResult:
    run_id: str
    agent: str
    status: RunStatus
    output: Optional[Dict[str, Any]] = None
    steps: List[StepRecord] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)
    degraded: bool = False
    rejected_findings: int = 0
    labels: Labels = Labels()
    detail: str = ""
    spec_hash: str = ""
