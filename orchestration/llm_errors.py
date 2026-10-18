"""Errors shared between providers and the router (kept outside `providers/` so the router never imports a
concrete provider)."""
from __future__ import annotations

from eternal_core.llm.base import LLMResponseError


class LLMBadOutput(LLMResponseError):
    """The provider answered, but the MODEL's output was unusable (empty, an attempted native tool call the
    provider rejected, JSON the provider itself refused). That is a model protocol failure, not an outage:
    the router hands `salvage` back as the reply text so the runner's strict parser rejects it, the turn is
    counted as invalid, and the bounded repair loop asks the model again. It is never silently accepted."""

    def __init__(self, message: str, salvage: str):
        super().__init__(message)
        self.salvage = salvage
