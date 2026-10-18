"""
Web search — read-only MCP server.

Eternal_Master_Build_Plan.md Phase 3: "Web search added, results routed
through INSPECT." See backend.py for the pluggable backend and the honest
note on why this sandbox runs a stub rather than a live provider.

Reads RCAP_WEB_SEARCH_POISON=1 from the environment to construct a
poisoning-enabled backend — used only by demo_exit_criteria.py, which sets
that variable on the subprocess it spawns for this one demonstration step.
A normal `python server.py` (or the registry's real deployment path) never
sets it, so poisoning is opt-in per-process, not a global default anyone
could trip over.

Flat, typed parameters — see cinemavault/server.py's module docstring for
why (a wrapped single-model parameter forces every caller to nest
arguments under a "params" key on the wire; flat parameters don't).
"""
import os
import sys
from pathlib import Path
from typing import Annotated, List

from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend import StubWebSearchBackend  # noqa: E402

mcp = FastMCP("web_search_mcp")

_poison = os.environ.get("RCAP_WEB_SEARCH_POISON") == "1"
_backend = StubWebSearchBackend(poison_generic_result=_poison)


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str


class WebSearchOutput(BaseModel):
    query: str
    results: List[SearchResult]


@mcp.tool(
    name="web_search",
    annotations={
        "title": "Search the web",
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": False, "openWorldHint": True,
    },
)
async def web_search(
    query: Annotated[str, Field(min_length=1, max_length=300, description="Search query")],
    max_results: Annotated[int, Field(ge=1, le=10, description="Maximum number of results")] = 3,
) -> WebSearchOutput:
    """Search the web and return a small number of results.

    Returns:
        WebSearchOutput: the original query plus a list of {title, url, snippet}.

    Note:
        This is the highest-risk tool in Phase 3's registry — the only one
        whose content genuinely originates outside anything the owner
        authored or reviewed. RCAP-P's gateway applies INSPECT to every
        result regardless of what this tool returns.
    """
    items = await _backend.search(query, max_results)
    return WebSearchOutput(
        query=query,
        results=[SearchResult(title=i.title, url=i.url, snippet=i.snippet) for i in items],
    )


if __name__ == "__main__":
    mcp.run()
