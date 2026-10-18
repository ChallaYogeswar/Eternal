"""
Same purpose as device_control_gateway/deployment.py: maps each server_key the tool
registry references to the actual (python executable, [script path])
command used to spawn it as an MCP subprocess. Phase 6 adds exactly one
new entry (`trading_engine`) on top of Phase 5's existing map.

`rcap_p_project_root` is threaded through unchanged, exactly as Phase 5
itself does relative to Phase 3/4 — the same reason: Phase 6's own
scripts resolve relative to `__file__` regardless of where the earlier
phases' roots happen to live.
"""
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from device_control_gateway.deployment import build_default_server_commands as build_phase5_default_commands
from device_control_gateway.deployment import build_demo_server_commands as build_phase5_demo_commands


def _phase6_server_commands() -> Dict[str, Tuple[str, List[str]]]:
    trading_engine_script = Path(__file__).resolve().parent.parent / "mcp_servers" / "trading_engine" / "server.py"
    return {"trading_engine": (sys.executable, [str(trading_engine_script)])}


def build_default_server_commands(rcap_p_project_root: Path) -> Dict[str, Tuple[str, List[str]]]:
    commands = dict(build_phase5_default_commands(rcap_p_project_root))
    commands.update(_phase6_server_commands())
    return commands


def build_demo_server_commands(rcap_p_project_root: Path) -> Dict[str, Tuple[str, List[str]]]:
    commands = dict(build_phase5_demo_commands(rcap_p_project_root))
    commands.update(_phase6_server_commands())
    return commands
