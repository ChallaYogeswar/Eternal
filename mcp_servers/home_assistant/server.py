"""
Home Assistant — MCP server for Phase 5, wrapping a subset of HA's REST
API. Runs co-located with Eternal's core; see data.py's module docstring
for the real-vs-fixture split and why self-containment here is precedent,
not structural necessity (unlike laptop_agent).

Tiering (device_control_gateway/registry_ext.py applies this; stated here for locality):
ha_lock/ha_unlock are SENSITIVE — build plan Section 6.3 point 2 names
"physical Home Assistant actions like locks" explicitly as SENSITIVE,
for BOTH directions (unlike laptop_agent's screen lock/unlock, where this
project draws its own reasoned distinction — see laptop_agent/server.py).
ha_turn_on/ha_turn_off (lights, switches) are WRITE. ha_get_state is
READ_ONLY.
"""
import sys
from pathlib import Path
from typing import Annotated, Any, Dict

from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data  # noqa: E402

mcp = FastMCP("home_assistant_mcp")


class EntityStateResult(BaseModel):
    entity_id: str
    domain: str
    state: str
    attributes: Dict[str, Any]


def _wrap(entity_state: "data.EntityState") -> EntityStateResult:
    return EntityStateResult(**entity_state.as_dict())


def _call(domain: str, service: str, entity_id: str) -> EntityStateResult:
    try:
        return _wrap(data.get_client().call_service(domain, service, entity_id))
    except data.HomeAssistantUnavailableError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="ha_lock",
    annotations={"title": "Lock a Home Assistant lock entity", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def ha_lock(entity_id: Annotated[str, Field(description="A lock.* entity_id, e.g. 'lock.front_door'")]) -> EntityStateResult:
    """Lock a physical door lock. SENSITIVE tier (build plan Section 6.3
    point 2, named explicitly) — never completable on voice alone."""
    return _call("lock", "lock", entity_id)


@mcp.tool(
    name="ha_unlock",
    annotations={"title": "Unlock a Home Assistant lock entity", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
)
async def ha_unlock(entity_id: Annotated[str, Field(description="A lock.* entity_id, e.g. 'lock.front_door'")]) -> EntityStateResult:
    """Unlock a physical door lock. SENSITIVE tier — never completable on
    voice alone, regardless of session trust score (build plan Section
    6.3 point 2)."""
    return _call("lock", "unlock", entity_id)


@mcp.tool(
    name="ha_turn_on",
    annotations={"title": "Turn on a light or switch", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def ha_turn_on(entity_id: Annotated[str, Field(description="A light.* or switch.* entity_id")]) -> EntityStateResult:
    """Turn on a light or switch. WRITE tier — no physical-security
    implication, unlike locks."""
    domain = entity_id.split(".", 1)[0]
    return _call(domain, "turn_on", entity_id)


@mcp.tool(
    name="ha_turn_off",
    annotations={"title": "Turn off a light or switch", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def ha_turn_off(entity_id: Annotated[str, Field(description="A light.* or switch.* entity_id")]) -> EntityStateResult:
    """Turn off a light or switch. WRITE tier."""
    domain = entity_id.split(".", 1)[0]
    return _call(domain, "turn_off", entity_id)


@mcp.tool(
    name="ha_get_state",
    annotations={"title": "Read a Home Assistant entity's state", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def ha_get_state(entity_id: Annotated[str, Field(description="Any registered entity_id")]) -> EntityStateResult:
    """Read-only state lookup — battery level, current lock state,
    on/off, etc. READ_ONLY tier."""
    try:
        return _wrap(data.get_client().get_state(entity_id))
    except data.HomeAssistantUnavailableError as e:
        raise ValueError(str(e)) from e


if __name__ == "__main__":
    mcp.run()
