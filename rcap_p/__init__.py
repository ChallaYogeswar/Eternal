"""
RCAP-P — personal-scale Rotate-Contain-Attribute Protocol.

Phase 3 of Eternal_Master_Build_Plan.md Section 8: "RCAP-P + First
Read-Only Capabilities." Implements the ring architecture from
RCAP_Whitepaper_CHALLAYOGESWAR.docx, scaled to a single-user assistant, the
same way qap_p/ (Phase 1) scaled down QAP. See README.md for the full
picture; each ring's own module docstring cites the specific whitepaper or
build-plan section it implements.
"""
from .gateway import RCAPGateway
from .config import Config
from .models import (
    CallerKind,
    ContainmentState,
    InspectVerdict,
    ToolCallContext,
    ToolCallResult,
    ToolManifestEntry,
    ToolSensitivity,
)

__all__ = [
    "RCAPGateway",
    "Config",
    "CallerKind",
    "ContainmentState",
    "InspectVerdict",
    "ToolCallContext",
    "ToolCallResult",
    "ToolManifestEntry",
    "ToolSensitivity",
]
