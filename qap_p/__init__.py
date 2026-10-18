"""QAP-P — Quiesce-Authorize Protocol, Personal scale.

Phase 1 substrate for the Eternal personal assistant project: device/session
state machine, hardware-backed peer authorization, trust scoring, a
hash-chained audit ledger, a scoped-token vault, canary detection, and
two-tier quarantine recovery — all running as one local daemon process
instead of the enterprise QAP specification's Raft-clustered Orchestrator.

See README.md for how this maps onto QAP_Whitepaper_CHALLAYOGESWAR.docx and
Eternal_Master_Build_Plan.md Section 6.1 / Section 8 (Phase 1).
"""
from .orchestrator import Orchestrator
from .config import Config
from .models import NodeState, DeviceRole, Device, Session

__all__ = ["Orchestrator", "Config", "NodeState", "DeviceRole", "Device", "Session"]
