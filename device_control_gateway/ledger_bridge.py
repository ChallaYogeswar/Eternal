"""
Ledger bridge — same pattern as eternal_core/audit/ledger_bridge.py
(Phase 2), rcap_p/ledger_bridge.py (Phase 3), and voice_consent_gateway/ledger_bridge.py
(Phase 4), applied here for the same reason.

Phase 5's laptop-agent actions, full-control handoffs, Home Assistant
calls, and kill-switch triggers all need to land in the *same*
tamper-evident audit trail every earlier phase already writes to — this is
the concrete mechanism behind the Phase 5 exit criterion's "full session
logging" clause, and behind the kill switch report being checkable against
a real ledger entry rather than a bare return value.

Prefers importing qap_p.ledger.Ledger (Phase 1's real implementation) so
that in an integrated deployment every laptop action, HA call, and
kill-switch trigger lands in the exact same hash-chain QAP-P, RCAP-P, and
Phase 4 already use. Falls back to a vendored, behavior-identical copy
(ledger.py in this package) when qap_p isn't installed alongside Phase 5.

Either way, Phase 5 keeps its own ledger *file* (own data_dir), separate
from the earlier phases' — this bridges *which class* writes the audit
trail, not *whose* file it writes to, exactly as every earlier phase
already established for this exact seam.
"""
try:
    from qap_p.ledger import Ledger  # type: ignore

    SOURCE = "qap_p (Phase 1, imported directly)"
except ImportError:
    from .ledger import Ledger  # vendored fallback, see module docstring

    SOURCE = "vendored fallback (qap_p not installed alongside Phase 5)"

__all__ = ["Ledger", "SOURCE"]
