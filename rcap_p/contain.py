"""
CONTAIN.

RCAP whitepaper Section 3.3: "CONTAIN is not a block... the session's route
back out of the ring is not re-advertised... the session is bound to a
resource-throttling policy... A session in CONTAIN transitions out in one
of three ways: reclassified as benign and released, exhausts a maximum
CONTAIN duration and is escalated to a hard block, or presents a valid
FASTLANE credential mid-session." Eternal_Master_Build_Plan.md Section 6.2,
personal-scale role: "Ambiguous-but-not-certain flags get sandboxed/dry-run
+ one manual approval, not a hard crash."

Personal-scale reading of "a live network session with an egress path":
there's no ongoing network session to leave half-open here — what's
"contained" in Phase 3 is a specific *piece of flagged content* (one tool
response), held out of the assistant's context and out of memory, pending
either your explicit approval or a timeout that escalates it to a hard
block. This is the direct, faithful translation of the whitepaper's own
design note ("appears to the attacker as a live, if slow or unstable,
target" -> here: appears to the graph as "still working on it," not as an
error and not as a silently-passed answer) into a single-user, no-network-
session context.

State machine, three states (models.ContainmentState):
    PASSTHROUGH  -> INSPECT verdict was CLEAN; content delivered normally.
    CONTAINED    -> SUSPICIOUS verdict; content held in the approval queue,
                    a safe placeholder returned in its place, until you
                    approve/reject or the max duration elapses (-> HARD_BLOCKED).
    HARD_BLOCKED -> MALICIOUS verdict, a repeat-offender source (ATTRIBUTE),
                    a DECEIVE trigger, or an expired CONTAINED item; content
                    is never delivered, full stop.
"""
import time
import uuid
from typing import Dict, List, Optional

from .config import Config
from .models import ApprovalRequest, ContainmentState, InspectVerdict


class ContainQueue:
    def __init__(self, config: Config, ledger):
        self.config = config
        self.ledger = ledger
        self._requests: Dict[str, ApprovalRequest] = {}

    def decide(self, verdict: InspectVerdict, is_repeat_offender: bool,
               deceive_triggered: bool) -> ContainmentState:
        """Pure decision function — no side effects, no queue writes.
        Kept separate from open()/approve()/reject() below so
        tests/test_contain.py can check the decision table exhaustively
        without needing a real queue or clock."""
        if deceive_triggered:
            return ContainmentState.HARD_BLOCKED
        if is_repeat_offender:
            return ContainmentState.HARD_BLOCKED
        if verdict == InspectVerdict.MALICIOUS:
            return ContainmentState.HARD_BLOCKED
        if verdict == InspectVerdict.SUSPICIOUS:
            return ContainmentState.CONTAINED
        return ContainmentState.PASSTHROUGH

    def open_approval_request(self, tool_name: str, source_label: str,
                               reasons: List[str], raw_content) -> ApprovalRequest:
        req = ApprovalRequest(
            request_id=str(uuid.uuid4()),
            tool_name=tool_name,
            source_label=source_label,
            reasons=reasons,
            raw_content=raw_content,
            created_at=time.time(),
        )
        self._requests[req.request_id] = req
        self.ledger.append("contain_approval_requested", {
            "request_id": req.request_id, "tool_name": tool_name,
            "source_label": source_label, "reasons": reasons,
        })
        return req

    def _expire_if_needed(self, req: ApprovalRequest) -> None:
        if req.resolved:
            return
        age = time.time() - req.created_at
        if age > self.config.contain_max_duration_seconds:
            req.resolved = True
            req.approved = False
            self.ledger.append("contain_approval_expired_hard_blocked", {
                "request_id": req.request_id, "age_seconds": age,
            })

    def approve(self, request_id: str) -> ApprovalRequest:
        req = self._require(request_id)
        self._expire_if_needed(req)
        if req.resolved:
            raise ValueError(f"request {request_id} already resolved (expired or previously decided)")
        req.resolved = True
        req.approved = True
        self.ledger.append("contain_approval_granted", {"request_id": request_id})
        return req

    def reject(self, request_id: str) -> ApprovalRequest:
        req = self._require(request_id)
        req.resolved = True
        req.approved = False
        self.ledger.append("contain_approval_rejected", {"request_id": request_id})
        return req

    def get(self, request_id: str) -> Optional[ApprovalRequest]:
        req = self._requests.get(request_id)
        if req:
            self._expire_if_needed(req)
        return req

    def _require(self, request_id: str) -> ApprovalRequest:
        req = self._requests.get(request_id)
        if req is None:
            raise KeyError(f"no approval request with id {request_id}")
        return req

    def pending(self) -> List[ApprovalRequest]:
        for req in self._requests.values():
            self._expire_if_needed(req)
        return [r for r in self._requests.values() if not r.resolved]
