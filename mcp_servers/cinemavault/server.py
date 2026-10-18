"""
CinemaVault — read-only MCP server.

Eternal_Master_Build_Plan.md Phase 3: "CinemaVault... registered as
read-only MCP server[s]." See data.py for the honest scoping note on what
this actually wraps (ÆTHER's real Activity(type=MOVIE) shape) versus what
remains an open architecture question (Section 9).

Every tool here is read-only by construction: none of them accept a write
path, and the underlying data.py module exposes no insert/update/delete
functions to this server layer at all — the only write-capable helper in
that module is reserved for the test suite's own direct use and is never
referenced anywhere below.

Parameters are flat, typed, `Annotated[..., Field(...)]` arguments rather
than a single wrapped input model — verified against a real MCP client
round trip that this keeps a tool's on-the-wire `arguments` dict exactly
as flat as a caller (this codebase's own gateway, an HTTP API consumer, or
a future phone app) would naturally construct it, with no extra nesting
convention to remember. (An earlier draft used one wrapped `params: XInput`
argument per tool; switched away from it after hitting the nesting
requirement it silently imposes on every caller — worth a real MCP round
trip to check rather than assuming, not just a style preference.)
"""
import sys
from pathlib import Path
from typing import Annotated, List, Optional

from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data  # noqa: E402

mcp = FastMCP("cinemavault_mcp")


class MovieRecord(BaseModel):
    id: str
    title: str
    watched_where: str
    watched_at: str
    rating: Optional[int] = None


@mcp.tool(
    name="cinemavault_list_movies",
    annotations={
        "title": "List logged movies",
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    },
)
async def cinemavault_list_movies(
    limit: Annotated[int, Field(ge=1, le=100, description="Maximum number of movies to return")] = 20,
    offset: Annotated[int, Field(ge=0, description="Number of movies to skip, for pagination")] = 0,
) -> List[MovieRecord]:
    """List movies logged in ÆTHER's Activity(type=MOVIE) module, most
    recently watched first.

    Returns:
        List[MovieRecord]: id, title, watched_where, watched_at (ISO 8601), rating (1-10 or null).
    """
    rows = data.list_movies(limit=limit, offset=offset)
    return [MovieRecord(**r) for r in rows]


@mcp.tool(
    name="cinemavault_get_movie",
    annotations={
        "title": "Get one logged movie",
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    },
)
async def cinemavault_get_movie(
    movie_id: Annotated[str, Field(min_length=1, description="Movie id, e.g. 'm1'")],
) -> MovieRecord:
    """Get a single logged movie by id.

    Returns:
        MovieRecord: id, title, watched_where, watched_at, rating.

    Raises:
        ValueError: if movie_id doesn't exist — the caller (RCAP-P's
            gateway) turns this into a clean error result, never a crash.
    """
    row = data.get_movie(movie_id)
    if row is None:
        raise ValueError(f"no movie logged with id '{movie_id}'")
    return MovieRecord(**row)


@mcp.tool(
    name="cinemavault_search_movies",
    annotations={
        "title": "Search logged movies by title",
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    },
)
async def cinemavault_search_movies(
    title_substring: Annotated[str, Field(min_length=1, max_length=200,
                                           description="Case-insensitive substring to match against movie titles")],
) -> List[MovieRecord]:
    """Search logged movies by a case-insensitive title substring.

    Returns:
        List[MovieRecord]: matches, most recently watched first. Empty list, not an error, if nothing matches.
    """
    rows = data.search_movies(title_substring)
    return [MovieRecord(**r) for r in rows]


if __name__ == "__main__":
    data.init_and_seed()
    mcp.run()
