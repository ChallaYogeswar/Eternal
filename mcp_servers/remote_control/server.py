"""
Remote control — Wake-on-LAN + full-control-handoff MCP server for Phase 5.

Runs co-located with Eternal's core (Pi/mini-PC), NOT on the target
laptop — see data.py's module docstring for why that separation is
structural, not stylistic. Deployment note: real placement is "always-on
box on the same home LAN segment as the controllable laptop," reachable
over Tailscale for the REST/MCP call itself, sending WoL over the local
broadcast domain. This sandbox has neither a second machine nor a home
LAN to broadcast on, so, like every other MCP server in this project, it
is launched as a local stdio subprocess for testing; tests target
loopback/unicast addresses rather than a real broadcast domain — see
tests/test_remote_control_data.py for the real, receipt-verified UDP round
trip this makes possible.

`remote_control_request_full_session` does not, by itself, decide whether
the caller is trusted enough — device_control_gateway.gateway additionally requires the
top-trust-tier check (device_control_gateway/trust_tier.py) on top of the ordinary
SENSITIVE-tier voice-gate/consent checks every other SENSITIVE tool gets,
exactly matching build plan Section 7.1's "the single most sensitive
action in the whole system."
"""
import sys
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data  # noqa: E402

mcp = FastMCP("remote_control_mcp")


class WakeResult(BaseModel):
    ok: bool
    target_mac: str
    bytes_sent: int
    sent_to: str
    detail: str


class FullControlHandoffResult(BaseModel):
    wake_on_lan_sent: bool
    target_mac_address: str
    protocol: str
    connection_hint: str
    session_id: str
    expires_at: float
    note: str


def _wrap_value_error(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except data.RemoteControlError as e:
        raise ValueError(str(e)) from e


@mcp.tool(
    name="remote_control_wake",
    annotations={"title": "Send Wake-on-LAN to a registered device", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def remote_control_wake(
    device_label: Annotated[str, Field(description=f"Must be one of: {', '.join(data.TARGET_DEVICES)}. No other value is accepted.")] = "primary_laptop",
) -> WakeResult:
    """Send a real Wake-on-LAN magic packet to one fixed, registered
    device. Waking a machine by itself does not expose control of it —
    WRITE tier (see device_control_gateway/registry_ext.py)."""
    if device_label not in data.TARGET_DEVICES:
        raise ValueError(f"'{device_label}' is not a registered target device: {list(data.TARGET_DEVICES)}")
    target = data.TARGET_DEVICES[device_label]
    outcome = data.send_wol(target.mac_address)
    return WakeResult(**outcome.as_dict())


@mcp.tool(
    name="remote_control_request_full_session",
    annotations={"title": "Request full remote control of a laptop", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
)
async def remote_control_request_full_session(
    device_label: Annotated[str, Field(description=f"Must be one of: {', '.join(data.TARGET_DEVICES)}. No other value is accepted.")] = "primary_laptop",
) -> FullControlHandoffResult:
    """Build plan Section 7.1: 'the single most sensitive action in the
    whole system.' Sends Wake-on-LAN if needed, then returns a handoff
    payload (connection hint for the configured remote-desktop protocol —
    RustDesk or OS-native, build plan Section 9 open item) — this tool
    never proxies keyboard/mouse/screen data itself. Reaching this
    function at all already means voice-gate, consent, AND device_control_gateway's own
    top-trust-tier gate all cleared (device_control_gateway/gateway.py) — nothing here
    re-checks that; this function's only job is doing the thing once
    asked, same layering as every other tool's data layer."""
    handoff = _wrap_value_error(data.request_full_control, device_label)
    return FullControlHandoffResult(**handoff.as_dict())


if __name__ == "__main__":
    mcp.run()
