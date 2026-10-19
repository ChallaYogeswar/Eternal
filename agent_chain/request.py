from dataclasses import dataclass
from typing import Optional

from voice_consent_gateway.models import WriteCallRequest


@dataclass
class AgentCallRequest(WriteCallRequest):
    """Everything a WriteCallRequest carries, plus the capability token. The token is consumed by CapabilityGateway
    and never forwarded: the inner chain receives a plain WriteCallRequest, so no existing gate or ledger entry can
    see or record it."""
    capability_token: Optional[str] = None
