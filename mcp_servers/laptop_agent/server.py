"""
Laptop agent — write-capable, physical-action MCP server for Phase 5.

Eternal_Master_Build_Plan.md Section 7.1: "small FastAPI service exposing
a *fixed, named* action list only... Never a generic 'run this command'
endpoint." Concretely: six tools below, each taking either no argument or
one name checked against a fixed allow-list (data.ALLOWED_APPS /
data.ALLOWED_SCRIPTS) — there is no tool here that accepts free-form text
to execute, by construction, not by convention.

Deployment note (test-harness honesty, same as Phase 2's Ollama/mini-PC
note): the build plan describes this as its own Tailscale-reachable
FastAPI service running ON the physical laptop. This sandbox has no
second machine and no Tailscale mesh, so — exactly like every other MCP
server in this project — it is launched as a local stdio subprocess for
testing. That transport choice is a test-harness simplification; the
fixed-action-list discipline and the state machine behind it are real.

None of these tools decide on their own whether they're allowed to run —
that's device_control_gateway.registry_ext (sensitivity tier), voice_consent_gateway.consent
(per-tool consent, reused unmodified), and voice_consent_gateway.voice_gate (voice-vs-
non-voice channel, reused unmodified), composed in device_control_gateway.gateway.
Phase 5 additionally requires laptop_unlock and laptop_shutdown to clear
the SAME SENSITIVE-tier voice-gate rule Phase 4 proved against a fixture —
this is the first phase where that rule protects something real.
"""
import sys
from pathlib import Path
from typing import Annotated, Optional

from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data  # noqa: E402

mcp = FastMCP("laptop_agent_mcp")


class LaptopActionResult(BaseModel):
    ok: bool
    action: str
    detail: str
    power_state: Optional[str] = None
    lock_state: Optional[str] = None


def _wrap(outcome: "data.LaptopActionOutcome") -> LaptopActionResult:
    return LaptopActionResult(**outcome.as_dict())


@mcp.tool(
    name="laptop_lock",
    annotations={"title": "Lock the laptop screen", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def laptop_lock() -> LaptopActionResult:
    """Lock the screen. The safe direction of the lock/unlock pair —
    locking a machine remotely has low downside even if triggered in
    error, so this is WRITE tier (see device_control_gateway/registry_ext.py's tiering
    rationale), unlike laptop_unlock below."""
    return _wrap(data.get_provider().lock())


@mcp.tool(
    name="laptop_unlock",
    annotations={"title": "Unlock the laptop screen", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
)
async def laptop_unlock() -> LaptopActionResult:
    """Unlock the screen. SENSITIVE tier: this is the risky direction of
    the lock/unlock pair — it exposes an active session to whoever is
    physically present, or to whatever automated step runs next. Never
    completable on voice alone (voice_consent_gateway.voice_gate, reused unmodified)."""
    return _wrap(data.get_provider().unlock())


@mcp.tool(
    name="laptop_sleep",
    annotations={"title": "Suspend the laptop", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def laptop_sleep() -> LaptopActionResult:
    """Suspend the machine. Safe direction (reduces exposure); WRITE tier."""
    return _wrap(data.get_provider().sleep())


@mcp.tool(
    name="laptop_open_app",
    annotations={"title": "Open an allow-listed application", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def laptop_open_app(
    app_name: Annotated[str, Field(description=f"Must be one of: {', '.join(data.ALLOWED_APPS)}. No other value is accepted.")],
) -> LaptopActionResult:
    """Open one pre-registered application. Raises if app_name is not on
    the fixed allow-list — this tool never accepts an arbitrary path or
    command, by construction (build plan: 'never a generic run-this-
    command endpoint')."""
    try:
        return _wrap(data.get_provider().open_app(app_name))
    except data.LaptopActionError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="laptop_run_script",
    annotations={"title": "Run a pre-registered named script", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
)
async def laptop_run_script(
    script_name: Annotated[str, Field(description=f"Must be one of: {', '.join(data.ALLOWED_SCRIPTS)}. No other value is accepted.")],
) -> LaptopActionResult:
    """Run one pre-registered named script — never arbitrary code, by
    construction. Same allow-list discipline as laptop_open_app."""
    try:
        return _wrap(data.get_provider().run_script(script_name))
    except data.LaptopActionError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="laptop_shutdown",
    annotations={"title": "Shut down the laptop", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
)
async def laptop_shutdown() -> LaptopActionResult:
    """Shut the machine down. SENSITIVE tier: high-consequence and not
    instantly reversible (a fresh Wake-on-LAN packet cannot power on a
    fully shut-down machine on most consumer hardware the way it can wake
    one from sleep) — never completable on voice alone."""
    return _wrap(data.get_provider().shutdown())


if __name__ == "__main__":
    mcp.run()
