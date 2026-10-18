"""
GatedMemoryWriter — Eternal_Master_Build_Plan.md Section 6.3, point 4:
"Memory writes go through the same INSPECT check as live tool calls. A bad
live tool call fails once. A bad memory write gets re-served as trusted
fact on every future recall until someone notices — a much longer-lived
compromise. No exception for 'it's just going into memory.'"

The key design decision here, stated explicitly: this class does NOT
reimplement pattern-scanning or schema-checking logic that merely
resembles rcap_p.inspect_ring's. It takes a live RCAPGateway instance (the
same one Phase4Gateway uses for tool calls) and calls its actual
`inspect_ring`, `attribute`, and `contain_queue` objects directly. A fact
that would have been flagged coming back from a tool call is flagged by
the literal same code path here — "the same INSPECT check," provably, not
by two implementations that happen to agree today and drift apart later.

Behavior:
  - CLEAN verdict -> committed to MemoryStore immediately.
  - SUSPICIOUS/MALICIOUS -> NOT committed as a trusted fact. Instead
    opened as a CONTAIN approval request (the exact same queue tool-call
    CONTAINED results use), so a flagged fact sits pending review instead
    of either (a) silently entering memory anyway or (b) silently
    vanishing with no record it was ever proposed.
"""
from dataclasses import dataclass
from typing import Optional

from rcap_p.models import ContainmentState, InspectVerdict

from ..ledger_bridge import Ledger
from ..models import MemoryCategory
from .store import Fact, MemoryStore


@dataclass
class MemoryWriteResult:
    ok: bool
    fact: Optional[Fact]
    containment_state: ContainmentState
    reasons: list
    approval_request_id: Optional[str] = None


class GatedMemoryWriter:
    def __init__(self, rcap_gateway, memory_store: MemoryStore, ledger: Ledger):
        self.rcap_gateway = rcap_gateway
        self.memory_store = memory_store
        self.ledger = ledger

    def write_fact(self, key: str, value: str, category: MemoryCategory, source: str) -> MemoryWriteResult:
        finding = self.rcap_gateway.inspect_ring.inspect(value, schema=None)

        source_label = f"memory_write:{source}"
        self.rcap_gateway.attribute.record_call(source_label)

        if finding.verdict != InspectVerdict.CLEAN:
            self.rcap_gateway.attribute.record_flag(source_label, finding.verdict, finding.reasons)

        containment_state = self.rcap_gateway.contain_queue.decide(
            verdict=finding.verdict,
            is_repeat_offender=self.rcap_gateway.attribute.is_repeat_offender(source_label),
            deceive_triggered=False,  # DECEIVE's canary-leak check is response-shaped; not applicable to a raw memory write
        )

        if containment_state == ContainmentState.PASSTHROUGH:
            fact = self.memory_store.write_fact(key=key, value=value, category=category, source=source)
            self.ledger.append("gated_memory_write_committed", {
                "key": key, "category": category.value, "source": source,
                "inspect_verdict": finding.verdict.value,
            })
            return MemoryWriteResult(ok=True, fact=fact, containment_state=containment_state, reasons=finding.reasons)

        if containment_state == ContainmentState.CONTAINED:
            req = self.rcap_gateway.contain_queue.open_approval_request(
                tool_name="memory_write", source_label=source_label, reasons=finding.reasons,
                raw_content={"key": key, "value": value, "category": category.value},
            )
            self.ledger.append("gated_memory_write_contained", {
                "key": key, "category": category.value, "source": source,
                "reasons": finding.reasons, "approval_request_id": req.request_id,
            })
            return MemoryWriteResult(
                ok=False, fact=None, containment_state=containment_state,
                reasons=finding.reasons, approval_request_id=req.request_id,
            )

        # HARD_BLOCKED
        self.ledger.append("gated_memory_write_blocked", {
            "key": key, "category": category.value, "source": source, "reasons": finding.reasons,
        })
        return MemoryWriteResult(ok=False, fact=None, containment_state=containment_state, reasons=finding.reasons)

    def approve_pending(self, request_id: str, key: str, category: MemoryCategory, source: str) -> MemoryWriteResult:
        """Commits a previously-CONTAINED fact after the owner reviews and
        approves it via the same approval queue tool-call CONTAINED
        results use (POST /contain/{id}/approve in api.py)."""
        req = self.rcap_gateway.contain_queue.approve(request_id)
        value = req.raw_content["value"]
        fact = self.memory_store.write_fact(key=key, value=value, category=category, source=f"{source}:approved_after_contain")
        self.ledger.append("gated_memory_write_approved_after_contain", {
            "key": key, "approval_request_id": request_id,
        })
        return MemoryWriteResult(ok=True, fact=fact, containment_state=ContainmentState.PASSTHROUGH, reasons=[])
