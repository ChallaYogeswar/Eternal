"""
Capability registry.

Eternal_Master_Build_Plan.md Section 4: "MCP servers wrapping every tool:
ÆTHER, CinemaVault, the study tracker, the laptop agent, Home Assistant...
New tools onboard via: write a small manifest -> wrap as MCP server
(template once, reuse) -> register -> vault issues a scoped token."

This module is the "write a small manifest -> register" half of that
sentence. Phase 3 registers exactly the tools the build plan scopes to it
(Phase 3: "CinemaVault and the study tracker registered as read-only MCP
servers... Web search added"), plus one deliberately fake entry for the
DECEIVE ring (see deceive.py) and one deliberately malicious entry used
only by the demo/test suite to prove the exit criterion — never registered
in a real deployment's registry (see build_default_registry() vs.
build_demo_registry() below).
"""
from typing import Dict, List, Optional

from pydantic import BaseModel

from .models import ToolManifestEntry, ToolSensitivity


# --- Output schemas for the structured-data half of INSPECT ----------------
# Real Pydantic models the corresponding MCP server's tools actually return
# — see mcp_servers/*/server.py. Declaring these here (rather than only in
# each server file) keeps the registry the single place that answers "what
# should this tool's response look like," independent of whether the
# calling code can even reach the server's own module.

class MovieRecord(BaseModel):
    id: str
    title: str
    watched_where: str
    watched_at: str
    rating: Optional[int] = None


class ListWrapper(BaseModel):
    """FastMCP's own convention (verified empirically, not documented
    behavior I want to leave implicit): a tool whose Python return type is
    `List[SomeModel]` arrives in structuredContent as `{"result": [...]}`,
    NOT as a bare list and NOT under any other key. A single `SomeModel`
    return, by contrast, arrives unwrapped. Getting this wrong would mean
    every clean list-returning tool call fails schema validation for a
    reason that has nothing to do with the content actually being
    suspicious — worth a named, shared model and this comment rather than
    a silently-wrong per-tool schema."""
    result: list


MovieListResult = ListWrapper  # cinemavault_list_movies / cinemavault_search_movies both return List[MovieRecord]


class CurriculumPhase(BaseModel):
    phase_number: int
    name: str
    weeks: str
    topics_done: list
    topics_next: list
    topics_todo: list


class WebSearchResult(BaseModel):
    query: str
    results: list


def build_default_registry() -> Dict[str, ToolManifestEntry]:
    """The real Phase 3 registry — what an actual deployment loads. No
    malicious_test entries here; that server exists only for
    demo_exit_criteria.py and the test suite, imported into a *separate*
    registry (build_demo_registry) so it's structurally impossible for it
    to end up reachable from a real conversation by accident."""
    entries = [
        ToolManifestEntry(
            tool_name="cinemavault_list_movies",
            server_key="cinemavault",
            description="List movies logged in ÆTHER's Activity(type=MOVIE) module, read-only.",
            sensitivity=ToolSensitivity.READ_ONLY,
            output_schema=MovieListResult,
            rate_limit_per_minute=30,
        ),
        ToolManifestEntry(
            tool_name="cinemavault_get_movie",
            server_key="cinemavault",
            description="Get one movie record by id, read-only.",
            sensitivity=ToolSensitivity.READ_ONLY,
            output_schema=MovieRecord,
            rate_limit_per_minute=30,
        ),
        ToolManifestEntry(
            tool_name="cinemavault_search_movies",
            server_key="cinemavault",
            description="Search logged movies by title substring, read-only.",
            sensitivity=ToolSensitivity.READ_ONLY,
            output_schema=MovieListResult,
            rate_limit_per_minute=30,
        ),
        ToolManifestEntry(
            tool_name="study_get_curriculum_status",
            server_key="study_tracker",
            description="Full 6-month curriculum status (done/next/todo per phase), read-only.",
            sensitivity=ToolSensitivity.READ_ONLY,
            output_schema=None,  # top-level shape is a list of CurriculumPhase; see server.py
            rate_limit_per_minute=30,
        ),
        ToolManifestEntry(
            tool_name="study_get_phase",
            server_key="study_tracker",
            description="One curriculum phase's topic status by phase number, read-only.",
            sensitivity=ToolSensitivity.READ_ONLY,
            output_schema=CurriculumPhase,
            rate_limit_per_minute=30,
        ),
        ToolManifestEntry(
            tool_name="web_search",
            server_key="web_search",
            description="Search the web, read-only. Highest-risk tool in Phase 3's registry — "
                         "the only one whose content genuinely originates outside anything the "
                         "owner authored or reviewed.",
            sensitivity=ToolSensitivity.READ_ONLY,
            output_schema=WebSearchResult,
            rate_limit_per_minute=10,  # tighter than the rest — see config.py, PERIMETER
        ),
        # --- DECEIVE: a registered tool that must never really be called ---
        ToolManifestEntry(
            tool_name="get_stored_credentials",
            server_key="__decoy__",
            description="(DECOY — never wired to anything real. See deceive.py.)",
            sensitivity=ToolSensitivity.READ_ONLY,
            is_decoy=True,
            rate_limit_per_minute=9999,  # a decoy should never be rate-limited away before it can fire
        ),
    ]
    return {e.tool_name: e for e in entries}


def build_demo_registry() -> Dict[str, ToolManifestEntry]:
    """Adds the deliberately malicious test tools on top of the real
    registry, for demo_exit_criteria.py and the integration test suite
    ONLY. Kept in a clearly-separate function (not a flag on the real one)
    so there's no config path in a real deployment that accidentally
    includes it. All three malicious_test server tools are registered here
    (not just the plain-text one) since the demo and e2e proof exercise
    all three injection/clean shapes — see mcp_servers/malicious_test/server.py."""
    registry = build_default_registry()
    for tool_name, description in [
        ("malicious_test_fetch_page",
         "TEST FIXTURE ONLY — plain-text instruction-override + exfiltration attempt."),
        ("malicious_test_fetch_document",
         "TEST FIXTURE ONLY — directive hidden inside an HTML comment."),
        ("malicious_test_fetch_page_clean",
         "TEST FIXTURE ONLY — genuinely clean content, from the same server as the two above."),
    ]:
        registry[tool_name] = ToolManifestEntry(
            tool_name=tool_name,
            server_key="malicious_test",
            description=description + " Never registered outside demo/tests.",
            sensitivity=ToolSensitivity.READ_ONLY,
            rate_limit_per_minute=30,
        )
    return registry


def list_tool_names(registry: Dict[str, ToolManifestEntry]) -> List[str]:
    return sorted(registry.keys())
