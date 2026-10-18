"""
Core data models for QAP-P (personal-scale Quiesce-Authorize Protocol).

Maps directly onto QAP_Whitepaper_CHALLAYOGESWAR.docx Section 3.1 (Node
States) and Eternal_Master_Build_Plan.md Section 6.1's personal-scale table:

    QAP concept                       Personal-scale equivalent
    ---------------------------------------------------------------------
    Node states (ACTIVE / QUIESCE /   Your devices & sessions: laptop,
    QUARANTINE)                       phone, browser session, MCP client
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class NodeState(str, Enum):
    """QAP whitepaper Section 3.1."""
    ACTIVE = "ACTIVE"
    QUIESCE = "QUIESCE"
    QUARANTINE = "QUARANTINE"


class DeviceRole(str, Enum):
    PHONE = "phone"
    LAPTOP = "laptop"
    BROWSER = "browser"
    MCP_CLIENT = "mcp_client"
    PI = "pi"
    MINI_PC = "mini_pc"


@dataclass
class Device:
    device_id: str
    role: DeviceRole
    public_key_pem: str
    hardware_backed: bool
    attestation_type: str
    enrolled_at: float
    quarantine_count: int = 0
    anomaly_count: int = 0


@dataclass
class Session:
    session_id: str
    device_id: str
    state: NodeState
    created_at: float
    last_transition_at: float
    quiesce_expires_at: Optional[float] = None
    quarantine_reason: Optional[str] = None
