"""
Per-run evidence registry (plan s3, invariant I6).

Only the ToolBroker holds the writer; everything else gets a read-only `EvidenceView`. A Finding's
`evidence_refs` and a Researcher's source URLs are valid only if they resolve here, so a model can
cite a tool result it really received but cannot invent one.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .contracts import Severity


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    tool: str
    content_hash: str
    refs: tuple            # source refs: file:path:lines, URLs, ledger hashes
    is_check: bool = False
    check_severity: Severity = Severity.INFO
    check_passed: Optional[bool] = None


class EvidenceView:
    def __init__(self, registry: "EvidenceRegistry"):
        self._r = registry

    def resolve(self, evidence_id: str) -> Optional[EvidenceRecord]:
        return self._r._records.get(evidence_id)

    def has_ref(self, ref: str) -> bool:
        return any(ref in rec.refs for rec in self._r._records.values())

    def ids(self) -> List[str]:
        return list(self._r._records)

    def checks(self) -> List[EvidenceRecord]:
        return [r for r in self._r._records.values() if r.is_check]


class EvidenceRegistry:
    """Writer side. Construct one per run; hand `.view()` to everyone but the broker."""

    def __init__(self) -> None:
        self._records: Dict[str, EvidenceRecord] = {}

    def register(self, tool: str, content: Any, refs: List[str], *, is_check: bool = False,
                 check_severity: Severity = Severity.INFO) -> EvidenceRecord:
        text = content if isinstance(content, str) else json.dumps(content, sort_keys=True, default=str)
        evidence_id = f"e{len(self._records) + 1}"
        passed = None
        if is_check and isinstance(content, dict) and isinstance(content.get("passed"), bool):
            passed = content["passed"]
        rec = EvidenceRecord(evidence_id, tool, hashlib.sha256(text.encode()).hexdigest(),
                             tuple(refs), is_check, check_severity, passed)
        self._records[evidence_id] = rec
        return rec

    def view(self) -> EvidenceView:
        return EvidenceView(self)


def reconcile_findings(findings: List[Any], view: EvidenceView):
    """Drop findings whose refs do not all resolve in this run's registry; enforce I8 (a model cannot
    lower a check-assigned severity or contradict a check result). Returns (kept, rejected_count).
    `findings` are Finding-like objects with .evidence_refs/.origin/.severity (mutated copies returned)."""
    from .contracts import FindingOrigin, SEVERITY_RANK

    kept, rejected = [], 0
    for f in findings:
        recs = [view.resolve(ref) for ref in f.evidence_refs]
        if not f.evidence_refs or any(r is None for r in recs):
            rejected += 1
            continue
        check_recs = [r for r in recs if r.is_check]
        if f.origin == FindingOrigin.CHECK and not check_recs:
            rejected += 1          # claims to come from a check but cites none
            continue
        severity = f.severity
        for r in check_recs:
            if SEVERITY_RANK[r.check_severity] > SEVERITY_RANK[severity]:
                severity = r.check_severity     # upward only
        kept.append(f.model_copy(update={"severity": severity}))
    return kept, rejected
