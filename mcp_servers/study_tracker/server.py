"""
Study tracker — read-only MCP server.

Eternal_Master_Build_Plan.md Phase 3: "the study tracker[,] registered as
[a] read-only MCP server." See data.py for what this wraps.

Flat, typed parameters — see cinemavault/server.py's module docstring for
why (a wrapped single-model parameter forces every caller to nest
arguments under a "params" key on the wire; flat parameters don't).
"""
import sys
from pathlib import Path
from typing import Annotated, List

from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data  # noqa: E402

mcp = FastMCP("study_tracker_mcp")


class CurriculumPhase(BaseModel):
    phase_number: int
    name: str
    weeks: str
    topics_done: List[str]
    topics_next: List[str]
    topics_todo: List[str]


@mcp.tool(
    name="study_get_curriculum_status",
    annotations={
        "title": "Get full curriculum status",
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    },
)
async def study_get_curriculum_status() -> List[CurriculumPhase]:
    """Full 6-month curriculum status across all five phases.

    Returns:
        List[CurriculumPhase]: each with phase_number, name, weeks, and
            topics broken into topics_done / topics_next / topics_todo.
    """
    rows = data.get_curriculum_status()
    return [CurriculumPhase(**r) for r in rows]


@mcp.tool(
    name="study_get_phase",
    annotations={
        "title": "Get one curriculum phase",
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    },
)
async def study_get_phase(
    phase_number: Annotated[int, Field(ge=1, le=5, description="Curriculum phase number, 1 through 5")],
) -> CurriculumPhase:
    """Get topic status for a single curriculum phase.

    Returns:
        CurriculumPhase.

    Raises:
        ValueError: if phase_number isn't a real phase.
    """
    row = data.get_phase(phase_number)
    if row is None:
        raise ValueError(f"no curriculum phase numbered {phase_number} (valid: 1-5)")
    return CurriculumPhase(**row)


if __name__ == "__main__":
    mcp.run()
