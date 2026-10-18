"""
RCAP-P (Phase 3) Configuration
------------------------------
Same pattern as qap_p/config.py (Phase 1) and eternal_core/config.py
(Phase 2): every tunable documented against the specific
Eternal_Master_Build_Plan.md or RCAP whitepaper line it implements.
"""
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    # --- Data / audit ---
    data_dir: str = os.environ.get("RCAP_DATA_DIR", "./data")
    ledger_db_path: str = ""

    # --- Ring 1 PERIMETER ---
    # "Rate-limit any externally-reachable surface (webhook, email-triggered
    # agent, web search)." Section 6.2. A sliding-window limiter keyed by
    # (source_label, tool_name) — see perimeter.py.
    perimeter_window_seconds: float = float(os.environ.get("RCAP_PERIMETER_WINDOW", 60))
    perimeter_default_limit_per_window: int = int(os.environ.get("RCAP_PERIMETER_LIMIT", 20))
    # Tarpit: once a source is over-limit, how long each subsequent
    # over-limit call is artificially delayed before its rejection is
    # returned — RCAP whitepaper Section 3.1: "tarpit-based throttling."
    # Kept short in Phase 3 (this is a personal single-user assistant, not
    # a public endpoint under real attack volume) but present and real.
    perimeter_tarpit_seconds: float = float(os.environ.get("RCAP_TARPIT_SECONDS", 0.25))

    # --- Ring 2 INSPECT ---
    # Whitepaper Section 3.1 (Ring 2), Eternal build plan Section 6.2:
    # "Two methods: strict schema/type validation for structured sources
    # ... cheaper and stronger, heuristic pattern-scanning for free text."
    inspect_suspicious_threshold: int = int(os.environ.get("RCAP_INSPECT_SUSPICIOUS_THRESHOLD", 1))
    inspect_malicious_threshold: int = int(os.environ.get("RCAP_INSPECT_MALICIOUS_THRESHOLD", 2))

    # --- Ring 3 ROTATE ---
    # "Session-scoped, expiring tool permissions" (Section 6.2), *not*
    # "session-scoped, expiring, but so long-lived nobody notices" — kept
    # deliberately short (5 minutes) so re-grant is a real, observable event
    # rather than a formality. Mirrors qap_p vault token TTLs in spirit.
    rotate_token_ttl_seconds: float = float(os.environ.get("RCAP_ROTATE_TTL", 300))

    # --- CONTAIN ---
    # RCAP whitepaper Section 3.3: "exhausts a maximum CONTAIN duration
    # (default: 300 seconds) and is escalated to a hard block." Personal
    # scale re-reading: this is wall-clock time an item may sit in the
    # approval queue before it's treated as abandoned/expired, not a live
    # network session duration — see contain.py docstring.
    contain_max_duration_seconds: float = float(os.environ.get("RCAP_CONTAIN_MAX_SECONDS", 300))

    # --- ATTRIBUTE ---
    # "Repeat offenders auto-contained without re-approval" (Section 6.2).
    # This is the repeat count at which a source stops getting a fresh
    # CONTAINED/approval-queue entry and is instead auto-hard-blocked on
    # sight.
    attribute_repeat_offender_threshold: int = int(os.environ.get("RCAP_REPEAT_OFFENDER_THRESHOLD", 3))

    # --- FASTLANE ---
    # Build plan Section 6.1: trust score "gates the sensitive-action tier
    # directly." FASTLANE reuses that same threshold concept for "is this
    # really you" rather than inventing a second number — see fastlane.py.
    fastlane_min_trust_score: float = float(os.environ.get("RCAP_FASTLANE_MIN_TRUST", 0.6))

    def __post_init__(self):
        os.makedirs(self.data_dir, exist_ok=True)
        self.ledger_db_path = self.ledger_db_path or os.path.join(self.data_dir, "rcap_ledger.db")
