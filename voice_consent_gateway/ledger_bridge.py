"""
Ledger bridge — same pattern as eternal_core/audit/ledger_bridge.py (Phase 2)
and rcap_p/ledger_bridge.py (Phase 3), applied here for the same reason.

Phase 4's consent grants, voice-gate denials, write-tool calls, and gated
memory writes all need to land in the *same* tamper-evident audit trail the
earlier phases already write to — this is the concrete mechanism behind
the Phase 4 exit criterion's "is logged with an explanation" clause having
any teeth: one hash-chain, not four disconnected ones that each have to be
separately trusted.

Prefers importing qap_p.ledger.Ledger (Phase 1's real implementation) so
that in an integrated deployment every consent grant, voice-gate decision,
and write call lands in the exact same hash-chain QAP-P and RCAP-P already
use. Falls back to a vendored, behavior-identical copy (ledger.py in this
package) when qap_p isn't installed alongside Phase 4, e.g. when Phase 4
is run or tested completely standalone.

Either way, Phase 4 keeps its own ledger *file* (own data_dir), separate
from the earlier phases' — this bridges *which class* writes the audit
trail, not *whose* file it writes to, exactly as Phase 2 and Phase 3 each
already established for this exact seam.
"""
try:
    from qap_p.ledger import Ledger  # type: ignore

    SOURCE = "qap_p (Phase 1, imported directly)"
except ImportError:
    from .ledger import Ledger  # vendored fallback, see module docstring

    SOURCE = "vendored fallback (qap_p not installed alongside Phase 4)"

__all__ = ["Ledger", "SOURCE"]
