"""
ATTRIBUTE.

RCAP whitepaper Section 3.4: "fingerprints TLS/JA3-style client
characteristics, timing and protocol-deviation signatures, and any
interaction with a Ring 4 honeytoken or decoy service, and correlates these
signals against prior CONTAIN sessions to build a picture of the
infrastructure directing the traffic ... rather than attempting to
identify an individual human operator." Eternal_Master_Build_Plan.md
Section 6.2: "Tags which source (site, sender, doc template, MCP server)
triggered a flag; repeat offenders auto-contained without re-approval."

Personal-scale reading: there's no TLS/JA3 fingerprint inside a one-user
assistant process, and the whitepaper's own Section 3.4 already reframes
the target as "the session and the infrastructure behind it, not a
person" — at personal scale that maps cleanly onto *source_label*: the
specific MCP server / tool / document template / search result domain a
piece of flagged content came from. That's the thing worth building a
reputation for, exactly as the build plan names it.

This module is deliberately the simplest ring: it's a counter with a
threshold, not a scoring model — matching Phase 1's own trust.py posture
("weights are intentionally conservative... Phase 1's job is to prove the
mechanism works, not hand-tune weights against real usage data that
doesn't exist yet"). The mechanism (count flags per source, auto-escalate
past a threshold) is real and tested; the threshold number is a documented
placeholder, same as qap_p/trust.py's weights are.
"""
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List

from .config import Config
from .models import InspectVerdict


@dataclass
class SourceRecord:
    source_label: str
    flag_count: int = 0
    total_calls: int = 0
    last_flag_reasons: List[str] = field(default_factory=list)
    last_flagged_at: float = 0.0
    hard_blocked: bool = False


class AttributeTracker:
    def __init__(self, config: Config, ledger):
        self.config = config
        self.ledger = ledger
        self._sources: Dict[str, SourceRecord] = defaultdict(lambda: SourceRecord(source_label=""))

    def _get(self, source_label: str) -> SourceRecord:
        rec = self._sources[source_label]
        if not rec.source_label:
            rec.source_label = source_label
        return rec

    def record_call(self, source_label: str) -> None:
        self._get(source_label).total_calls += 1

    def record_flag(self, source_label: str, verdict: InspectVerdict, reasons: List[str]) -> SourceRecord:
        """Called by the gateway whenever INSPECT or DECEIVE returns
        anything other than CLEAN/not-triggered. Returns the updated record
        so the gateway can immediately check is_repeat_offender() without a
        second lookup."""
        rec = self._get(source_label)
        rec.flag_count += 1
        rec.last_flag_reasons = reasons
        rec.last_flagged_at = time.time()
        self.ledger.append("attribute_flag_recorded", {
            "source_label": source_label,
            "verdict": verdict.value,
            "reasons": reasons,
            "cumulative_flag_count": rec.flag_count,
        })
        if self.is_repeat_offender(source_label) and not rec.hard_blocked:
            rec.hard_blocked = True
            self.ledger.append("attribute_source_auto_hard_blocked", {
                "source_label": source_label,
                "flag_count": rec.flag_count,
                "threshold": self.config.attribute_repeat_offender_threshold,
            })
        return rec

    def is_repeat_offender(self, source_label: str) -> bool:
        return self._get(source_label).flag_count >= self.config.attribute_repeat_offender_threshold

    def is_hard_blocked(self, source_label: str) -> bool:
        return self._get(source_label).hard_blocked

    def report(self) -> List[SourceRecord]:
        """Full standing per-source reputation — the human-readable half of
        'a visible, diffable audit trail,' surfaced here rather than only
        buried in raw ledger rows."""
        return sorted(self._sources.values(), key=lambda r: r.flag_count, reverse=True)

    def reset(self) -> None:
        """Test/demo helper only."""
        self._sources.clear()
