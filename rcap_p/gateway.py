"""
RCAPGateway — the orchestrator tying every ring together, one call at a
time. Same role for RCAP-P that qap_p/orchestrator.py plays for QAP-P:
every other module in this package is a mechanism; this is the thing that
actually sequences them into the protocol the whitepaper describes.

Pipeline for one tool call (see call_tool below for the exact sequence
and — importantly — a documented, deliberate deviation from a literal
reading of the whitepaper's own ring diagram):

    1.  Look up the tool in the capability registry. Unknown tool -> a
        clean error result, never an exception.
    2.  DECEIVE's decoy-tool check and ATTRIBUTE's repeat-offender check
        run FIRST, before anything else, and REGARDLESS of FASTLANE. Both
        are about the *tool/source*'s own standing, not the caller's — a
        decoy tool is fake no matter who calls it or whether they ever
        held a valid permission token for it, and a source with three
        prior injection attempts doesn't become safe because the owner
        happened to be the one asking this time. Running these before
        PERIMETER/ROTATE matters concretely: nobody legitimate would ever
        go through the real grant flow for a tool that was never meant to
        be called, so if this check ran *after* ROTATE, a decoy-tool call
        would always get masked by an ordinary "no permission token"
        rejection instead of being recognized as what it is.
    3.  FASTLANE check (fastlane.py). Owner-interactive, ACTIVE QAP-P
        session, hardware-backed device, trust above threshold -> grant.
    4.  If FASTLANE was NOT granted: PERIMETER (rate limit) and ROTATE
        (permission token) gate the request. Both are about *caller*
        legitimacy, and FASTLANE existing is precisely what exempts a
        caller from needing them re-proven per-call.
    5.  CORE: the real, mediated call happens (via `tool_executor`, which
        the real deployment backs with mcp_client.py's stdio MCP client,
        and tests back with a plain async fake — see mcp_client.py and
        tests/conftest.py). Any exception the tool itself raises is caught
        here and returned as a clean error result, never propagated —
        this is the "not... hard-crashed" half of the Phase 3 exit
        criterion, applied to ordinary tool failures, not just malicious
        content.
    6.  INSPECT scans the response — ALWAYS, even under FASTLANE. This is
        the same "content-side, not caller-side" reasoning as step 2,
        applied to the response instead of the request: INSPECT protects
        the assistant's context/memory from what a tool *returns*, which
        has nothing to do with who asked the question. The owner asking
        "what's playing tonight" doesn't make a compromised search result
        any less dangerous to hand to the LLM afterward. Whether FASTLANE
        should exempt content-side checks at all is exactly the kind of
        question the source whitepaper's own diagram leaves ambiguous (it
        draws FASTLANE bypassing "all 4 rings" without distinguishing
        caller-side gates from content-side ones) — this is Phase 3's own
        resolution of that ambiguity, spelled out here and in the README
        rather than left implicit.
    7.  ATTRIBUTE records a flag if INSPECT or DECEIVE's leak-check found
        anything. CONTAIN then decides PASSTHROUGH / CONTAINED /
        HARD_BLOCKED from the combined verdict, and — this is the "not
        silently passed" half of the exit criterion — CONTAINED and
        HARD_BLOCKED results always carry a clear, visible placeholder,
        never the raw flagged content and never a bare failure.
"""
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from .attribute import AttributeTracker
from .config import Config
from .contain import ContainQueue
from .deceive import DeceiveRing
from .fastlane import FastlaneGate
from .inspect_ring import InspectRing
from .models import (
    CallerKind,
    ContainmentState,
    InspectVerdict,
    ToolCallContext,
    ToolCallResult,
    ToolManifestEntry,
)
from .perimeter import PerimeterLimiter
from .rotate import RotateRing

ToolExecutor = Callable[[str, Dict[str, Any]], Awaitable[Any]]


class RCAPGateway:
    def __init__(
        self,
        config: Config,
        registry: Dict[str, ToolManifestEntry],
        ledger,
        tool_executor: ToolExecutor,
        trust_lookup=None,
        canary_service=None,
    ):
        self.config = config
        self.registry = registry
        self.ledger = ledger
        self.tool_executor = tool_executor

        self.perimeter = PerimeterLimiter(config)
        for name, entry in registry.items():
            self.perimeter.set_limit_for_tool(name, entry.rate_limit_per_minute)

        self.inspect_ring = InspectRing(config)
        self.rotate = RotateRing(config, ledger)
        self.deceive = DeceiveRing(ledger, canary_service=canary_service)
        self.attribute = AttributeTracker(config, ledger)
        self.contain_queue = ContainQueue(config, ledger)
        self.fastlane = FastlaneGate(config, trust_lookup=trust_lookup)

    def grant_tool_permission(self, session_id: str, tool_name: str) -> str:
        """Thin passthrough so callers only ever import the gateway, not
        every ring module individually."""
        return self.rotate.grant(session_id, tool_name)

    async def call_tool(
        self,
        ctx: ToolCallContext,
        tool_name: str,
        arguments: Dict[str, Any],
        rotate_token: Optional[str] = None,
    ) -> ToolCallResult:
        manifest = self.registry.get(tool_name)
        if manifest is None:
            return ToolCallResult(
                tool_name=tool_name, ok=False, content=None,
                containment_state=ContainmentState.HARD_BLOCKED, fastlane=False,
                inspect_finding=None, attributed_source=None,
                error=f"unknown tool '{tool_name}' — not present in the capability registry",
            )

        self.attribute.record_call(ctx.source_label)

        # --- content/tool-integrity gates: run FIRST, before any caller-side
        # gate, and regardless of FASTLANE. A decoy tool call is unambiguous
        # evidence the moment it happens; it must never be masked by an
        # ordinary "no permission token" or "rate limited" rejection just
        # because nobody legitimate would ever have gone through the real
        # grant flow for a tool that was never meant to be called at all.
        # Same reasoning for a known repeat-offender source: its standing
        # is about the SOURCE's own track record, independent of whether
        # this particular caller happens to be trusted. ---
        decoy_verdict = self.deceive.check_decoy_call(tool_name, ctx.source_label, manifest.is_decoy)
        if decoy_verdict.triggered:
            self.attribute.record_flag(ctx.source_label, InspectVerdict.MALICIOUS, [decoy_verdict.reason])
            return ToolCallResult(
                tool_name=tool_name, ok=False,
                content=f"[RCAP-P BLOCKED] {decoy_verdict.reason}",
                containment_state=ContainmentState.HARD_BLOCKED, fastlane=False,
                inspect_finding=None, attributed_source=ctx.source_label, error=decoy_verdict.reason,
            )

        if self.attribute.is_hard_blocked(ctx.source_label):
            reason = f"source '{ctx.source_label}' is auto-hard-blocked after repeated flags (ATTRIBUTE)"
            self.ledger.append("attribute_preemptive_block", {
                "tool_name": tool_name, "source_label": ctx.source_label,
            })
            return ToolCallResult(
                tool_name=tool_name, ok=False, content=f"[RCAP-P BLOCKED] {reason}",
                containment_state=ContainmentState.HARD_BLOCKED, fastlane=False,
                inspect_finding=None, attributed_source=ctx.source_label, error=reason,
            )

        fastlane_decision = self.fastlane.check(ctx)

        # --- caller-side gates: skipped only when FASTLANE is genuinely granted ---
        if not fastlane_decision.granted:
            perimeter_decision = self.perimeter.check(ctx.source_label, tool_name)
            if not perimeter_decision.allowed:
                if perimeter_decision.tarpitted_seconds > 0:
                    await asyncio.sleep(perimeter_decision.tarpitted_seconds)
                self.ledger.append("perimeter_rejected", {
                    "tool_name": tool_name, "source_label": ctx.source_label,
                    "reason": perimeter_decision.reason,
                })
                return ToolCallResult(
                    tool_name=tool_name, ok=False, content=None,
                    containment_state=ContainmentState.HARD_BLOCKED, fastlane=False,
                    inspect_finding=None, attributed_source=None, error=perimeter_decision.reason,
                )

            rotate_decision = self.rotate.check(ctx.session_id or "", tool_name, rotate_token)
            if not rotate_decision.allowed:
                self.ledger.append("rotate_rejected", {
                    "tool_name": tool_name, "session_id": ctx.session_id,
                    "reason": rotate_decision.reason,
                })
                return ToolCallResult(
                    tool_name=tool_name, ok=False, content=None,
                    containment_state=ContainmentState.HARD_BLOCKED, fastlane=False,
                    inspect_finding=None, attributed_source=None, error=rotate_decision.reason,
                )

        # --- CORE: the real, mediated, logged call ---
        ledger_hashes = []
        try:
            raw_content = await self.tool_executor(tool_name, arguments)
        except Exception as e:  # noqa: BLE001 — deliberately broad: no tool failure may escape as a crash
            h = self.ledger.append("tool_call_execution_error", {
                "tool_name": tool_name, "source_label": ctx.source_label, "error": str(e),
            })
            return ToolCallResult(
                tool_name=tool_name, ok=False, content=None,
                containment_state=ContainmentState.HARD_BLOCKED, fastlane=fastlane_decision.granted,
                inspect_finding=None, attributed_source=None,
                ledger_entry_hashes=[h], error=f"tool execution failed: {e}",
            )

        ledger_hashes.append(self.ledger.append("tool_call_core", {
            "tool_name": tool_name, "source_label": ctx.source_label,
            "session_id": ctx.session_id, "fastlane": fastlane_decision.granted,
        }))

        # --- INSPECT + DECEIVE(leak) on the response, always ---
        finding = self.inspect_ring.inspect(raw_content, schema=manifest.output_schema)
        text_repr = raw_content if isinstance(raw_content, str) else json.dumps(raw_content, default=str)
        leak_verdict = self.deceive.check_response_for_canary_leak(text_repr, tool_name, ctx.source_label)

        flagged = finding.verdict != InspectVerdict.CLEAN or leak_verdict.triggered
        if flagged:
            combined_reasons = list(finding.reasons)
            if leak_verdict.triggered:
                combined_reasons.append(leak_verdict.reason)
            effective_verdict = finding.verdict if finding.verdict != InspectVerdict.CLEAN else InspectVerdict.MALICIOUS
            self.attribute.record_flag(ctx.source_label, effective_verdict, combined_reasons)

        containment_state = self.contain_queue.decide(
            verdict=finding.verdict,
            is_repeat_offender=self.attribute.is_repeat_offender(ctx.source_label),
            deceive_triggered=leak_verdict.triggered,
        )

        if containment_state == ContainmentState.PASSTHROUGH:
            return ToolCallResult(
                tool_name=tool_name, ok=True, content=raw_content,
                containment_state=containment_state, fastlane=fastlane_decision.granted,
                inspect_finding=finding, attributed_source=None, ledger_entry_hashes=ledger_hashes,
            )

        reasons = finding.reasons + ([leak_verdict.reason] if leak_verdict.triggered else [])
        if containment_state == ContainmentState.CONTAINED:
            req = self.contain_queue.open_approval_request(tool_name, ctx.source_label, reasons, raw_content)
            placeholder = (
                f"[RCAP-P CONTAIN] The response from '{tool_name}' (source: '{ctx.source_label}') was "
                f"flagged as suspicious and is held pending your approval — not shown until reviewed. "
                f"Reasons: {'; '.join(reasons) or 'schema/pattern check flagged this content'}. "
                f"Approval request id: {req.request_id}."
            )
            return ToolCallResult(
                tool_name=tool_name, ok=True, content=placeholder,
                containment_state=containment_state, fastlane=fastlane_decision.granted,
                inspect_finding=finding, attributed_source=ctx.source_label,
                ledger_entry_hashes=ledger_hashes,
            )

        # HARD_BLOCKED
        placeholder = (
            f"[RCAP-P BLOCKED] The response from '{tool_name}' (source: '{ctx.source_label}') was "
            f"blocked, not delivered. Reasons: {'; '.join(reasons) or 'high-confidence malicious content'}."
        )
        return ToolCallResult(
            tool_name=tool_name, ok=False, content=placeholder,
            containment_state=containment_state, fastlane=fastlane_decision.granted,
            inspect_finding=finding, attributed_source=ctx.source_label,
            ledger_entry_hashes=ledger_hashes, error="content blocked by RCAP-P (CONTAIN: hard_blocked)",
        )
