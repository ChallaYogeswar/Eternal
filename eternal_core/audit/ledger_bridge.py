"""
Ledger bridge.

Eternal_Master_Build_Plan.md Section 6.1: the hash-chained ledger should
record "every memory write, tool call, and external request." Phase 2's
only write of any kind is this audit entry — recording that a cloud LLM
call happened, which provider served it, and whether the answer was
degraded — never anything to ÆTHER, CinemaVault, the filesystem beyond
Eternal's own data dir, or any tracked external system. This is *why*
"zero write access to anything" (the Phase 2 exit criterion) and "audit
every external request" (Section 6.1) aren't in tension: one is about the
assistant's capabilities toward the outside world, the other is the
assistant's own operational logging, and this ledger is only ever the
latter.

Prefers importing qap_p.ledger.Ledger — Phase 1's real implementation — so
that in an integrated deployment (both phases installed together, as the
build plan's phasing implies: "Phase 2... sitting on top of the substrate
proven [in Phase 1]") Eternal's audit trail runs through the exact same
mechanism QAP-P itself uses. Falls back to audit/ledger.py (a vendored,
behavior-identical copy — qap_p/ledger.py has zero dependency on the rest
of the qap_p package, so vendoring it is exact, not an approximation) when
qap_p isn't installed alongside Phase 2, e.g. when Phase 2 is being run or
tested completely standalone. Either way, Phase 2 keeps its own ledger
file, separate from Phase 1's (different machine, different process, own
data_dir) — this bridges *which class* writes the audit trail, not *whose*
ledger file it writes to.
"""
try:
    from qap_p.ledger import Ledger  # type: ignore

    SOURCE = "qap_p (Phase 1, imported directly)"
except ImportError:
    from .ledger import Ledger  # vendored fallback, see module docstring

    SOURCE = "vendored fallback (qap_p not installed alongside Phase 2)"

__all__ = ["Ledger", "SOURCE"]
