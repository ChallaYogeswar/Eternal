"""
ToolBroker — the ONLY object in this package that holds a gateway reference (invariant I2).

Per call it enforces, in order: session alive -> allowlist -> repeat cap -> tool-call cap -> argument
rules (reserved `_` keys, size, schema) -> consent for WRITE -> session alive AGAIN immediately before
dispatch (kill TOCTOU) -> gateway call under a timeout. It stamps a fixed identity on every call
(AUTOMATED_ROUTINE; `source_label = agent:<name>` — stable per agent so RCAP PERIMETER/ATTRIBUTE state
persists across runs, amendment A1), registers evidence only for successful non-decoy results, labels
every observation, and never retries around a gateway denial.

The broker is gateway-agnostic: it takes any object with
`call_tool(*, session_id, tool_name, arguments, source_label, explanation, confirmation_channel,
caller_kind, rotate_token)` — i.e. the existing Phase6CoreGateway — and imports none of the phase packages.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol

from ._timeout import CallTimeout, call_with_timeout
from .contracts import (AgentSpec, Labels, Provenance, RunStatus, Sensitivity, SpecError, ToolInfo, ToolTier)
from .evidence import EvidenceRegistry

CALLER_KIND = "automated_routine"        # rcap_p.models.CallerKind.AUTOMATED_ROUTINE — never FASTLANE (I3)


class ConsentPort(Protocol):
    def check(self, tool_name: str, run_id: str) -> str:
        """'granted' | 'pending' | 'denied'"""


class NoWriteConsent:
    """Fail-closed default: no WRITE call is ever approved. Real per-run grants: orchestration.consent.ConsentStore."""
    def check(self, tool_name: str, run_id: str) -> str:
        return "pending"


class StaticConsent:
    def __init__(self, granted=()):
        self.granted = set(granted)

    def check(self, tool_name: str, run_id: str) -> str:
        return "granted" if tool_name in self.granted else "pending"


def validate_spec_against_catalog(spec: AgentSpec, catalog: Dict[str, ToolInfo]) -> None:
    prov = set()
    for name in spec.allowed_tools:
        info = catalog.get(name)
        if info is None:
            raise SpecError(f"spec '{spec.name}' lists unknown tool '{name}'")
        if info.tier >= ToolTier.SENSITIVE:
            raise SpecError(f"spec '{spec.name}' lists SENSITIVE tool '{name}' (I15)")
        if info.tier > spec.max_tool_tier:
            raise SpecError(f"tool '{name}' ({info.tier.name}) exceeds spec ceiling ({spec.max_tool_tier.name})")
        if info.tier >= ToolTier.WRITE and not spec.can_write:
            raise SpecError(f"tool '{name}' is WRITE but spec '{spec.name}' has can_write=False")
        prov.add(info.output_provenance)
    if Provenance.WEB in prov and Provenance.RAG_INTERNAL in prov:
        raise SpecError(f"spec '{spec.name}' combines web and RAG tools in one run (I5)")


@dataclass
class Verdict:
    ok: bool
    content: Any
    reason: str
    decoy: bool


def normalize_verdict(raw: Any) -> Verdict:
    def get(o, k, d=None):
        if o is None:
            return d
        return o.get(k, d) if isinstance(o, dict) else getattr(o, k, d)

    ok = bool(get(raw, "ok", False))
    content = get(raw, "content")
    under = get(raw, "underlying_result")
    cs = get(under, "containment_state") if under is not None else get(raw, "containment_state")
    cs = getattr(cs, "value", cs)
    decoy = cs == "hard_blocked" or (isinstance(content, str) and content.startswith("[RCAP-P BLOCKED]"))
    reason = get(raw, "denied_reason") or get(under, "error") or ("" if ok else "gateway denied the call")
    return Verdict(ok=ok and not decoy, content=content, reason=str(reason or ""), decoy=decoy)


@dataclass
class BrokerOutcome:
    kind: str                                   # ok | denied | terminal
    observation: str
    verdict: str
    labels: Optional[Labels] = None
    decoy: bool = False
    evidence_id: Optional[str] = None
    terminal_status: Optional[RunStatus] = None
    pending_tool: Optional[str] = None


def _canon(args: Dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, default=str)


class ToolBroker:
    def __init__(self, *, spec: AgentSpec, catalog: Dict[str, ToolInfo], gateway: Any, session_id: str,
                 run_id: str, evidence: EvidenceRegistry, consent: Optional[ConsentPort] = None,
                 rotate_token_provider: Optional[Callable[[str], Optional[str]]] = None,
                 session_alive: Optional[Callable[[], bool]] = None, origin_channel: str = "typed",
                 token_provider: Optional[Callable[[], Optional[str]]] = None):
        validate_spec_against_catalog(spec, catalog)
        self.spec, self.catalog, self._gateway = spec, catalog, gateway
        self.session_id, self.run_id, self.evidence = session_id, run_id, evidence
        self.consent = consent or NoWriteConsent()
        self._rotate = rotate_token_provider or (lambda tool: None)
        self._alive = session_alive or (lambda: True)
        self.origin_channel = origin_channel
        self._token = token_provider            # opaque capability token; never shown to the model
        self.calls = 0
        self.denials = 0
        self._repeat: Dict[str, int] = {}

    # -- helpers ---------------------------------------------------------------------------------
    def _deny(self, reason: str) -> BrokerOutcome:
        self.denials += 1
        if self.denials >= self.spec.budget.max_denials:
            return BrokerOutcome("terminal", f"[call denied: {reason}]", f"denied:{reason}",
                                 terminal_status=RunStatus.DENIED_OUT)
        return BrokerOutcome("denied", f"[call denied: {reason}]", f"denied:{reason}")

    def _killed(self) -> BrokerOutcome:
        return BrokerOutcome("terminal", "[run stopped: session no longer active]", "killed",
                             terminal_status=RunStatus.KILLED)

    def _truncate(self, text: str) -> str:
        cap = self.spec.budget.max_observation_chars
        return text if len(text) <= cap else f"{text[:cap]}…[truncated, {len(text)} chars]"

    # -- the one entry point ---------------------------------------------------------------------
    def execute(self, tool: str, arguments: Dict[str, Any], reason: str) -> BrokerOutcome:
        b = self.spec.budget
        if not self._alive():
            return self._killed()
        info = self.catalog.get(tool)
        if tool not in self.spec.allowed_tools or info is None:
            return self._deny("tool_not_allowed")
        if not isinstance(arguments, dict):
            return self._deny("arguments_must_be_an_object")
        sig = hashlib.sha256(f"{tool}|{_canon(arguments)}".encode()).hexdigest()
        if self._repeat.get(sig, 0) >= b.max_repeat_calls:
            return self._deny("repeat_call_blocked")
        if self.calls >= b.max_tool_calls:
            return BrokerOutcome("terminal", "[run stopped: tool-call budget exhausted]", "budget_tool_calls",
                                 terminal_status=RunStatus.BUDGET_EXHAUSTED)
        self._repeat[sig] = self._repeat.get(sig, 0) + 1
        if any(str(k).startswith("_") for k in arguments):
            return self._deny("reserved_argument_key")
        if len(_canon(arguments).encode()) > b.max_arg_bytes:
            return self._deny("arguments_too_large")
        if info.args_model is not None:
            try:
                arguments = info.args_model.model_validate(arguments).model_dump(mode="json")
            except Exception as e:  # noqa: BLE001 — pydantic ValidationError and friends
                return self._deny(f"invalid_arguments: {str(e)[:200]}")
        if info.tier >= ToolTier.WRITE:
            decision = self.consent.check(tool, self.run_id)
            if decision == "pending":
                return BrokerOutcome("terminal", f"[run paused: '{tool}' needs confirmation]",
                                     "awaiting_confirmation", terminal_status=RunStatus.AWAITING_CONFIRMATION,
                                     pending_tool=tool)
            if decision != "granted":
                return self._deny("consent_denied")
        token = None
        if self._token is not None:
            token = self._token()
            if not token:
                return self._deny("no_capability_token")
        if not self._alive():                       # TOCTOU: re-check immediately before dispatch (I9)
            return self._killed()

        self.calls += 1
        kwargs = dict(
            session_id=self.session_id, tool_name=tool, arguments=arguments,
            source_label=f"agent:{self.spec.name}", explanation=f"[run {self.run_id}] {reason}"[:500],
            confirmation_channel="voice" if self.origin_channel == "voice" else "automated",
            caller_kind=CALLER_KIND, rotate_token=self._rotate(tool),
        )
        if token is not None:
            kwargs["capability_token"] = token
        try:
            raw = call_with_timeout(lambda: self._gateway.call_tool(**kwargs), b.per_call_timeout_s)
        except CallTimeout:
            return self._deny("tool_timeout")
        except Exception as e:  # noqa: BLE001 — a tool failure is an observation, not a crash
            return self._deny(f"tool_error:{type(e).__name__}")

        v = normalize_verdict(raw)
        if not v.ok:
            out = self._deny(v.reason[:200] or "gateway_denied")
            out.decoy = v.decoy
            return out                               # decoy / denied results are never evidence (I7)

        text = v.content if isinstance(v.content, str) else json.dumps(v.content, sort_keys=True, default=str)
        refs: List[str] = []
        if info.source_extractor:
            try:
                refs = list(info.source_extractor(arguments, v.content))
            except Exception:  # noqa: BLE001
                refs = []
        rec = self.evidence.register(tool, v.content, refs, is_check=info.is_check,
                                     check_severity=info.check_severity)
        labels = Labels(info.output_sensitivity, frozenset({info.output_provenance}), info.output_pii)
        return BrokerOutcome("ok", self._truncate(text), "ok", labels=labels, evidence_id=rec.evidence_id)


class BrokerFactory:
    """Built once by the composition root (the only place that ever sees the gateway) and handed to
    AgentRunner, so the runtime never holds a gateway reference itself (I2)."""

    def __init__(self, gateway: Any, catalog: Dict[str, ToolInfo]):
        self._gateway = gateway
        self.catalog = catalog

    def make(self, **kwargs: Any) -> ToolBroker:
        return ToolBroker(gateway=self._gateway, catalog=self.catalog, **kwargs)
