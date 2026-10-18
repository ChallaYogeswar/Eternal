"""
Kill switch — Eternal_Master_Build_Plan.md Section 7.1, verbatim: "a kill
switch that immediately quarantines every session and revokes every
scoped token." Section 8 (Phase 5 exit criteria): "the kill switch has
actually been tested."

This is new Phase 5 code, not a modification of qap_p or rcap_p — every
capability it needs already exists as a public method on Orchestrator,
Store, Vault, and RotateRing (checked directly against their source, not
assumed):

    qap_p.orchestrator.Orchestrator.store.list_devices()          -> every enrolled device
    qap_p.orchestrator.Orchestrator.store.list_sessions_for_device -> every session for a device
    qap_p.orchestrator.Orchestrator.quarantine_session(id, reason) -> per-session quarantine (Phase 1)
    qap_p.orchestrator.Orchestrator.vault.revoke_all_for_device(id) -> kills every QAP-P vault token for a device
    rcap_p.rotate.RotateRing.revoke_session(id)                    -> kills every RCAP-P ROTATE token for a session

This function is the composition of those five calls, looped over every
device/session in the system, plus one ledger entry — nothing here
duplicates their logic.

Design decision, stated explicitly: beyond the two things the build plan
names (sessions, tokens), this also revokes CONSENT (voice_consent_gateway.consent) for
every SENSITIVE-tier tool specifically — not WRITE-tier, so pulling the
switch doesn't force re-granting ordinary ÆTHER consent as collateral
damage. This is a deliberate superset of the literal build-plan wording,
not a substitute for it: session quarantine + token revocation alone are
already sufficient to stop any in-flight or newly-attempted call (a new
session can't act without a fresh ROTATE token, which is trust-gated), so
the consent revocation is defence-in-depth insurance for the specific
tier the build plan is most worried about — re-granting it afterward is a
small, deliberate cost, not an oversight. Pass consent_registry=None (and/
or registry=None) to skip this step in contexts where Phase 4 isn't wired
in (e.g., a QAP-P/RCAP-P-only test); everything else still runs.
"""
import time
from typing import TYPE_CHECKING, Dict, List, Optional

from .ledger_bridge import Ledger
from .models import KillSwitchReport

if TYPE_CHECKING:  # pragma: no cover
    from qap_p.orchestrator import Orchestrator
    from rcap_p.gateway import RCAPGateway
    from rcap_p.models import ToolManifestEntry
    from voice_consent_gateway.consent import ConsentRegistry


def pull_kill_switch(
    orchestrator: "Orchestrator",
    rcap_gateway: "RCAPGateway",
    ledger: Ledger,
    *,
    consent_registry: Optional["ConsentRegistry"] = None,
    registry: Optional[Dict[str, "ToolManifestEntry"]] = None,
    reason: str = "manual_trigger",
) -> KillSwitchReport:
    from qap_p.state_machine import NodeState  # local import: kill switch is the only Phase 5 module that needs this

    devices = orchestrator.store.list_devices()
    quarantined: List[str] = []
    already_quarantined: List[str] = []
    vault_tokens_revoked = 0
    rotate_tokens_revoked = 0

    for device in devices:
        vault_tokens_revoked += orchestrator.vault.revoke_all_for_device(device.device_id)
        for session in orchestrator.store.list_sessions_for_device(device.device_id):
            rotate_tokens_revoked += rcap_gateway.rotate.revoke_session(session.session_id)
            if session.state == NodeState.QUARANTINE:
                already_quarantined.append(session.session_id)
                continue
            orchestrator.quarantine_session(session.session_id, reason=f"KILL_SWITCH: {reason}")
            quarantined.append(session.session_id)

    consent_revoked_for_tools: List[str] = []
    if consent_registry is not None and registry is not None:
        from rcap_p.models import ToolSensitivity

        for tool_name, manifest in registry.items():
            if manifest.sensitivity == ToolSensitivity.SENSITIVE and consent_registry.has_consent(tool_name):
                consent_registry.revoke(tool_name, revoked_by="kill_switch", note=reason)
                consent_revoked_for_tools.append(tool_name)

    h = ledger.append("phase5_kill_switch_pulled", {
        "reason": reason,
        "devices_seen": len(devices),
        "sessions_quarantined": quarantined,
        "sessions_already_quarantined": already_quarantined,
        "vault_tokens_revoked": vault_tokens_revoked,
        "rotate_tokens_revoked": rotate_tokens_revoked,
        "consent_revoked_for_tools": consent_revoked_for_tools,
    })

    return KillSwitchReport(
        triggered_at=time.time(),
        reason=reason,
        devices_seen=len(devices),
        sessions_quarantined=quarantined,
        sessions_already_quarantined=already_quarantined,
        vault_tokens_revoked=vault_tokens_revoked,
        rotate_tokens_revoked=rotate_tokens_revoked,
        consent_revoked_for_tools=consent_revoked_for_tools,
        ledger_entry_hash=h,
    )
