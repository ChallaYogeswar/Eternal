"""Provider-neutral tool execution contract for the Eternal graph.

The graph deliberately knows only how to hand a structured request to a
security-owned gateway. It never imports a phase-specific gateway or calls a
tool executor directly.
"""
from typing import Any, Dict, Protocol


class ToolGateway(Protocol):
    def call_tool(
        self,
        *,
        session_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        source_label: str,
        explanation: str,
    ) -> Any:
        """Execute one request through the owning security gateway."""


def result_to_dict(result: Any) -> Dict[str, Any]:
    """Convert dataclass, Pydantic, or mapping results to plain state data."""
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "__dict__"):
        return dict(result.__dict__)
    return {"ok": False, "content": None, "error": f"unsupported tool result type: {type(result).__name__}"}
