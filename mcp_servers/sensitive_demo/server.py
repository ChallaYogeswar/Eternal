"""
Demo-only SENSITIVE-tier fixture — mirrors the isolation pattern Phase 3
used for mcp_servers/malicious_test: a real, callable MCP server that is
never wired into the default (production) registry, existing only to give
the voice-gate rule ("no sensitive-tier action can complete on voice
alone," build plan Phase 4 exit criterion) something concrete to prove
itself against, over a real stdio MCP round trip rather than an in-process
fake.

None of Phase 4's actual required capabilities (ÆTHER's tracker writes)
are SENSITIVE tier — the build plan reserves that tier for Phase 5/6's
"trade execution, full laptop control, physical Home Assistant actions
like locks" (Section 6.3, point 2), none of which exist yet. Rather than
either (a) mis-classifying an ordinary tracker write as SENSITIVE just to
have something to test against, or (b) leaving the SENSITIVE-tier
enforcement path completely unexercised until Phase 5, this fixture
stands in for "whatever Phase 5/6 eventually registers here" — a
physical-action tool (door lock), the same category of action the build
plan itself names as the canonical sensitive-tier example.

This tool never actually controls anything — it's a Python function that
returns a string. The point being tested is entirely upstream of it: does
Phase4Gateway correctly refuse to let a call reach this tool at all when
the confirmation channel is VOICE, regardless of trust/consent/FASTLANE?
"""
from typing import Annotated

from pydantic import Field
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("sensitive_demo_mcp")

_unlock_log = []  # in-memory, test/demo-inspectable record of "doors" this fixture has "unlocked"


@mcp.tool(
    name="sensitive_demo_unlock_door",
    annotations={"title": "[DEMO/FIXTURE] Unlock a door", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
)
async def sensitive_demo_unlock_door(
    door: Annotated[str, Field(min_length=1, max_length=100, description="Which door, e.g. 'front'")],
) -> str:
    """DEMO/FIXTURE ONLY — never registered in the production registry
    (see voice_consent_gateway/registry_ext.py). Stands in for Phase 5/6's real physical
    Home Assistant lock control. If this function ever actually runs
    during a test, that means Phase 4's voice-gate/consent checks let a
    SENSITIVE-tier call through them — the test asserting the opposite is
    the actual point, not this function's body.
    """
    _unlock_log.append(door)
    return f"[FIXTURE] would have unlocked the {door} door — no such thing exists, this is a Phase 4 demo tool."


if __name__ == "__main__":
    mcp.run()
