"""
Real MCP client, backing the gateway's `tool_executor` for an actual
deployment (as opposed to the plain async fakes used in most of the unit
test suite — see tests/conftest.py).

Eternal_Master_Build_Plan.md Section 4: "Capability registry: MCP servers
wrapping every tool... New tools onboard via: write a small manifest ->
wrap as MCP server (template once, reuse) -> register." This module is the
"reuse" half — one client, one way of reaching any registered server,
regardless of which tool within it is being called.

Each registered tool's `server_key` (registry.py) maps to one MCP server
process, launched over stdio and kept alive for the pool's lifetime rather
than re-spawned per call — spawning a Python interpreter per tool call
would make PERIMETER's rate limiting the least of the latency problems.
"""
import asyncio
from contextlib import AsyncExitStack
from typing import Any, Dict, Optional

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class MCPServerPool:
    """Keeps one live ClientSession per server_key, spawned lazily on
    first use. `server_commands` maps server_key -> (command, args) — see
    build_default_server_commands() below for what a real Phase 3
    deployment points this at."""

    def __init__(self, server_commands: Dict[str, "tuple[str, list]"]):
        self._server_commands = server_commands
        self._sessions: Dict[str, ClientSession] = {}
        self._stack = AsyncExitStack()
        self._lock = asyncio.Lock()

    async def _get_session(self, server_key: str) -> ClientSession:
        if server_key in self._sessions:
            return self._sessions[server_key]
        async with self._lock:
            if server_key in self._sessions:  # re-check after acquiring the lock
                return self._sessions[server_key]
            if server_key not in self._server_commands:
                raise KeyError(f"no MCP server registered for server_key '{server_key}'")
            command, args = self._server_commands[server_key]
            params = StdioServerParameters(command=command, args=args)
            read, write = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._sessions[server_key] = session
            return session

    async def call(self, server_key: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        session = await self._get_session(server_key)
        result = await session.call_tool(tool_name, arguments)
        if result.isError:
            text = result.content[0].text if result.content else "unknown MCP tool error"
            raise RuntimeError(f"MCP tool '{tool_name}' on server '{server_key}' returned an error: {text}")
        # FastMCP tools that return a Pydantic model or dict/list arrive as
        # structuredContent when present; plain-string tools arrive as
        # content[0].text. Prefer structuredContent since it round-trips
        # types (int/list/dict) that a plain-text field would flatten.
        if result.structuredContent is not None:
            return result.structuredContent
        if result.content:
            return result.content[0].text
        return None

    async def aclose(self) -> None:
        await self._stack.aclose()
        self._sessions.clear()

    def make_executor(self, tool_to_server: Dict[str, str]):
        """Returns an async (tool_name, arguments) -> Any callable matching
        gateway.ToolExecutor's signature, resolving each tool_name to its
        server via the capability registry's server_key (registry.py).

        Suitable for a SINGLE-TASK caller that owns this pool's whole
        lifecycle end to end — e.g. scripts/test_mcp_e2e.py, which creates
        the pool, makes every call, and closes it all within one `main()`
        coroutine. NOT suitable for an ASGI server: anyio's stdio
        transport ties each connection's cancel scope to the asyncio Task
        that opened it, and a typical ASGI server runs every request in
        its own Task — a session lazily opened inside request A's task and
        later closed during a shutdown-event task (or reused from request
        B's task) raises "Attempted to exit cancel scope in a different
        task than it was entered in." rcap_p/api.py uses
        `make_stateless_executor` below instead, for exactly this reason —
        confirmed by hitting this exact failure while smoke-testing the
        daemon during Phase 3 development, not a theoretical concern.
        """

        async def executor(tool_name: str, arguments: Dict[str, Any]) -> Any:
            server_key = tool_to_server.get(tool_name)
            if server_key is None:
                raise KeyError(f"tool '{tool_name}' has no server_key mapping")
            return await self.call(server_key, tool_name, arguments)

        return executor


def make_stateless_executor(server_commands: Dict[str, "tuple[str, list]"], tool_to_server: Dict[str, str]):
    """Alternative to MCPServerPool.make_executor, safe for an ASGI server
    (or any caller where each call may run in its own asyncio Task): opens
    a fresh stdio connection, makes exactly one call, and tears the
    connection down — all inside the SAME `async with` block, hence the
    same Task, every time. No cross-request state, so there's no cancel
    scope to violate.

    The cost is real: a fresh Python subprocess per tool call instead of a
    kept-alive one. Deliberately accepted here rather than engineering
    around it (e.g. a dedicated owner-task-plus-queue architecture) — a
    single-user personal assistant's real call volume doesn't need
    kept-alive MCP sessions to feel responsive, and "simple and correct"
    beats "fast and subtly broken across requests" at this scale. If a
    future phase's call volume ever makes subprocess-spawn latency a real
    problem, that dedicated-owner-task architecture is the documented next
    step — not a reason to ship the pooled version's bug in the meantime.
    """

    async def executor(tool_name: str, arguments: Dict[str, Any]) -> Any:
        server_key = tool_to_server.get(tool_name)
        if server_key is None:
            raise KeyError(f"tool '{tool_name}' has no server_key mapping")
        if server_key not in server_commands:
            raise KeyError(f"no MCP server registered for server_key '{server_key}'")
        command, args = server_commands[server_key]
        params = StdioServerParameters(command=command, args=args)
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


def build_tool_to_server_map(registry: Dict[str, Any]) -> Dict[str, str]:
    return {name: entry.server_key for name, entry in registry.items()}
