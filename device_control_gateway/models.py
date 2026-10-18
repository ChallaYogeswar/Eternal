"""
Core data models for Phase 5 (Remote Device Control + Home Assistant) —
gateway-level vocabulary only.

Same posture as rcap_p/models.py (Phase 3) and voice_consent_gateway/models.py (Phase 4):
plain dataclasses, no behavior. Unlike those two, this file is
deliberately SMALL: every earlier phase's own MCP servers (aether,
cinemavault, study_tracker) define their OWN local Pydantic result models
inside their own server.py, rather than importing shared ones from
voice_consent_gateway/models.py or rcap_p/models.py — checked directly against
mcp_servers/aether/server.py before writing this file, not assumed. Phase
5's laptop_agent, remote_control, and home_assistant servers follow that
exact precedent (see each server.py) for the same reason stated there:
laptop_agent in particular must be able to run standalone on a physical
machine that never has the `device_control_gateway` package installed on it at all, so
nothing tool-specific belongs here. What's left is only what device_control_gateway's own
gateway/killswitch/api code needs to share across module boundaries
within THIS package.
"""
from dataclasses import dataclass
from typing import List


@dataclass
class KillSwitchReport:
    """Build plan Section 7.1: 'a kill switch that immediately quarantines
    every session and revokes every scoped token.' Every field here is
    something the exit criterion's 'kill switch has actually been tested'
    clause needs to be checkable against, not just an ok=True."""
    triggered_at: float
    reason: str
    devices_seen: int
    sessions_quarantined: List[str]
    sessions_already_quarantined: List[str]
    vault_tokens_revoked: int
    rotate_tokens_revoked: int
    consent_revoked_for_tools: List[str]
    ledger_entry_hash: str
