"""
Env-propagating stateless MCP executor — closes a real, verified gap in
rcap_p.mcp_client.make_stateless_executor (and MCPServerPool), found
while wiring HOME_ASSISTANT_BASE_URL/HOME_ASSISTANT_TOKEN through for
Phase 5's real Home Assistant client.

Checked directly against mcp.client.stdio.stdio_client's source before
concluding this, not assumed: when StdioServerParameters.env is None
(true for every StdioServerParameters(...) construction in
rcap_p.mcp_client — grep-checked, neither MCPServerPool nor
make_stateless_executor ever sets env=), the spawned subprocess receives
ONLY mcp.client.stdio.get_default_environment()'s minimal set (HOME,
PATH, TERM on this platform) — NOT this process's os.environ. This means
no environment variable set on a parent daemon/script process — not
PHASE5_HA_REAL, not HOME_ASSISTANT_BASE_URL, not PHASE5_LAPTOP_STATE_PATH,
and, worth noting for awareness rather than action, not even Phase 3/4's
own AETHER_DB_PATH-style configuration — ever reaches a subprocess-
spawned MCP server under the current wiring. This was harmless for every
tool built before Phase 5, since none of their env-configurable defaults
needed to vary per run for the scenarios those phases tested. It stops
being harmless the moment a real deployment needs to point
RealHomeAssistantClient at an actual HA instance: without a fix, that
configuration is silently unreachable no matter how the daemon process
itself is configured.

Fixing this correctly belongs in rcap_p.mcp_client (thread env through
StdioServerParameters) — deliberately NOT done there: rcap_p is Phase 3,
already validated, and changing its function signatures is out of Phase
5's scope to do unilaterally. This module is the composition-based
alternative: a near-identical copy of make_stateless_executor's own logic
(same "vendor with attribution" pattern as device_control_gateway/ledger.py relative to
qap_p/ledger.py), with exactly one addition — merging an explicit
`extra_env` dict into what the subprocess receives. Phase 3/4's existing
servers are unaffected by extra env vars being present (they are
self-contained and simply don't read ones they don't know about); this
executor can safely be used for ALL tool calls, not only Phase 5's,
which is exactly how device_control_gateway/api.py and device_control_gateway/deployment.py wire it in —
one executor, not two, so there is no risk of a tool silently using the
wrong one.

Recommended real fix, for the record (not implemented here, per the
above): add `env: Optional[Dict[str, str]] = None` to
rcap_p.mcp_client.make_stateless_executor and MCPServerPool.__init__,
merged with get_default_environment() the same way this module does, and
retire this module once that lands.
"""
from typing import Any, Callable, Dict, Optional, Tuple

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client


def make_env_aware_stateless_executor(
    server_commands: Dict[str, Tuple[str, list]],
    tool_to_server: Dict[str, str],
    extra_env: Optional[Dict[str, str]] = None,
) -> Callable[[str, Dict[str, Any]], Any]:
    """Same contract and same fresh-subprocess-per-call behavior as
    rcap_p.mcp_client.make_stateless_executor — see that function's own
    docstring for why stateless (not pooled) is the correct choice for an
    ASGI daemon. The only difference is the `env=` line below."""
    merged_env = {**get_default_environment(), **(extra_env or {})}

    async def executor(tool_name: str, arguments: Dict[str, Any]) -> Any:
        server_key = tool_to_server.get(tool_name)
        if server_key is None:
            raise KeyError(f"tool '{tool_name}' has no server_key mapping")
        if server_key not in server_commands:
            raise KeyError(f"no MCP server registered for server_key '{server_key}'")
        command, args = server_commands[server_key]
        params = StdioServerParameters(command=command, args=args, env=dict(merged_env))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                if result.isError:
                    text = result.content[0].text if result.content else "unknown MCP tool error"
                    raise RuntimeError(f"MCP tool '{tool_name}' on server '{server_key}' returned an error: {text}")
                if result.structuredContent is not None:
                    return result.structuredContent
                if result.content:
                    return result.content[0].text
                return None

    return executor
