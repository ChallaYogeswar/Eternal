"""Adapter that lets the Phase 2 graph use the complete Phase 6 gateway.

This adapter carries no authorization logic. It only translates the
phase-neutral graph request into Phase 4's request model; Phase 6 remains the
single owner of consent, ROTATE, trust, regulatory, and MCP execution gates.
"""
import asyncio
from typing import Any, Dict, Optional

from voice_consent_gateway.models import ConfirmationChannel, WriteCallRequest

from .gateway import Phase6Gateway


class Phase6CoreGateway:
    def __init__(self, gateway: Phase6Gateway):
        self.gateway = gateway

    def call_tool(
        self,
        *,
        session_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        source_label: str,
        explanation: str,
        confirmation_channel: str = "text_typed",
        caller_kind: str = "owner_interactive",
        rotate_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        request = WriteCallRequest(
            tool_name=tool_name,
            arguments=arguments,
            session_id=session_id,
            confirmation_channel=ConfirmationChannel(confirmation_channel),
            source_label=source_label,
            explanation=explanation,
            rotate_token=rotate_token,
            caller_kind=caller_kind,
        )
        result = asyncio.run(self.gateway.call_tool(request))
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return dict(result.__dict__)