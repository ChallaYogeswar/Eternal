"""
Ledger bridge — same pattern as eternal_core/audit/ledger_bridge.py in
Phase 2, applied here for the same reason.

RCAP-P's CONTAIN ledger entries and ATTRIBUTE fingerprints (whitepaper
Section 6.6, "Immutable Attribution Ledger") need to land in the *same*
tamper-evident audit trail QAP-P and Eternal Core already write to — the
RCAP whitepaper says as much directly: "both protocols share a distributed
Orchestrator design pattern, and a joint deployment can use a single
consensus cluster and a single trust-scoring subsystem for both."

Prefers importing qap_p.ledger.Ledger — Phase 1's real implementation — so
that in an integrated deployment (all three phases installed side by side,
as the build plan's own phasing implies) every tool call, containment
event, and attribution record RCAP-P produces lands in the exact same
hash-chain QAP-P's own session/device events already use. Falls back to a
vendored, behavior-identical copy (ledger.py in this package) when qap_p
isn't installed alongside RCAP-P, e.g. when Phase 3 is run or tested
completely standalone.

Either way, RCAP-P keeps its own ledger *file* (own data_dir), separate
from Phase 1's and Phase 2's — this bridges *which class* writes the audit
trail, not *whose* file it writes to, exactly as Phase 2 already
established for this exact seam.
"""
try:
    from qap_p.ledger import Ledger  # type: ignore

    SOURCE = "qap_p (Phase 1, imported directly)"
except ImportError:
    from .ledger import Ledger  # vendored fallback, see module docstring

    SOURCE = "vendored fallback (qap_p not installed alongside Phase 3)"

__all__ = ["Ledger", "SOURCE"]
