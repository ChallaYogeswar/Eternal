"""
Ledger bridge — same pattern as eternal_core/audit/ledger_bridge.py
(Phase 2), rcap_p/ledger_bridge.py (Phase 3), voice_consent_gateway/ledger_bridge.py
(Phase 4), and device_control_gateway/ledger_bridge.py (Phase 5), applied here for the
same reason.

Every signal generated, every paper order filled, every live-order gate
decision (allowed or refused, and why), and every circuit-breaker trigger
lands in the *same* tamper-evident audit trail every earlier phase
already writes to — see gateway.py for the actual call sites.

Prefers importing qap_p.ledger.Ledger (Phase 1's real implementation) so
that in an integrated deployment, a trading decision is checkable against
the exact same hash-chain as a QAP-P session event or an RCAP-P tool call.
Falls back to a vendored, behavior-identical copy (ledger.py in this
package) when qap_p isn't installed alongside Phase 6.

Either way, Phase 6 keeps its own ledger *file* (its own data_dir),
separate from the earlier phases' — this bridges *which class* writes the
audit trail, not *whose file* it writes to, exactly as every earlier
phase already established for this exact seam.
"""
try:
    from qap_p.ledger import Ledger  # type: ignore

    SOURCE = "qap_p (Phase 1, imported directly)"
except ImportError:
    from .ledger import Ledger  # vendored fallback, see module docstring

    SOURCE = "vendored fallback (qap_p not installed alongside Phase 6)"

__all__ = ["Ledger", "SOURCE"]
