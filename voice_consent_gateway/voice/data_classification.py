"""
Outbound data classification — Eternal_Master_Build_Plan.md Section 6.3,
point 3: "Outbound data classification (RCAP-P above only covers inbound).
First concrete draft: Finance/trading figures, health-adjacent tracker
entries, and dream-journal/memory content stay local-only — Coqui/pyttsx3,
Ollama, never ElevenLabs or a cloud LLM call. Casual conversational output
may use cloud TTS/LLM freely."

This is deliberately the mirror image of rcap_p.inspect_ring: INSPECT
decides whether *inbound* content is safe to admit into context. This
module decides whether *outbound* content is safe to hand to a
third-party cloud service. Same "flag, don't silently guess" posture:
`classify()` returns its reasons, not just a verdict, so a caller (or a
test) can see why.

Two independent signals are checked, and either one alone is enough to
force LOCAL_ONLY (defense in depth, the same philosophy INSPECT uses when
it scans free text even from a schema-clean structured source):
  1. `domain_hint` — the caller states which ÆTHER/tracker domain this
     content came from (e.g. a `finance` tool response literally IS
     finance data, no text pattern-matching required to know that).
  2. A pattern scan over the text itself — a safety net for content whose
     domain wasn't tagged, or wasn't tagged correctly.
"""
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from ..models import MemoryCategory


class DataClass(str, Enum):
    LOCAL_ONLY = "local_only"
    CLOUD_OK = "cloud_ok"


@dataclass
class ClassificationResult:
    data_class: DataClass
    reasons: List[str] = field(default_factory=list)


# Kept intentionally small and readable, same posture as
# rcap_p.inspect_ring's PATTERN_GROUPS: illustrative, not exhaustive: a
# real deployment tunes and grows this list against its own traffic.
_FINANCE_PATTERNS = [
    r"\brupees?\b", r"\brs\.?\s?\d", r"₹", r"\bexpense\b", r"\bexpenses\b",
    r"\btrade\b", r"\btrading\b", r"\bstock\b", r"\bportfolio\b",
    r"\bbalance\b", r"\bprofit\b", r"\bloss(?:es)?\b", r"\bkite connect\b",
    r"\bbuy\b.{0,15}\bshares?\b", r"\bsell\b.{0,15}\bshares?\b",
]
_HEALTH_PATTERNS = [
    r"\bblood pressure\b", r"\bbp\b", r"\bmedication\b", r"\bsymptom\b",
    r"\bdoctor\b", r"\bweight\b.{0,10}\bkg\b", r"\bwaist\b.{0,10}\bcm\b",
    r"\bworkout log\b", r"\bhealth\b",
]
_DREAM_MEMORY_PATTERNS = [
    r"\bdream journal\b", r"\bdream\b", r"\bmemories\b", r"\bmemory\b", r"\bdiary\b",
]

_ALWAYS_LOCAL_DOMAINS = {
    MemoryCategory.FINANCE, MemoryCategory.HEALTH, MemoryCategory.DREAM_JOURNAL,
}


def _scan(text: str, patterns: List[str]) -> bool:
    lowered = text.lower()
    return any(re.search(p, lowered) for p in patterns)


def classify(text: str, domain_hint: Optional[MemoryCategory] = None) -> ClassificationResult:
    reasons: List[str] = []

    if domain_hint in _ALWAYS_LOCAL_DOMAINS:
        reasons.append(f"domain_hint={domain_hint.value} is always local-only per build plan Section 6.3.3")
        return ClassificationResult(DataClass.LOCAL_ONLY, reasons)

    if _scan(text, _FINANCE_PATTERNS):
        reasons.append("text matched a finance/trading pattern")
    if _scan(text, _HEALTH_PATTERNS):
        reasons.append("text matched a health-adjacent pattern")
    if _scan(text, _DREAM_MEMORY_PATTERNS):
        reasons.append("text matched a dream-journal/memory pattern")

    if reasons:
        return ClassificationResult(DataClass.LOCAL_ONLY, reasons)

    return ClassificationResult(DataClass.CLOUD_OK, ["no local-only domain or pattern matched — casual/conversational"])
