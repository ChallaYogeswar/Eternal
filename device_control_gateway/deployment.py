"""
Phase 5 deployment wiring — same separation of concerns as
rcap_p/deployment.py (Phase 3) and voice_consent_gateway/deployment.py (Phase 4): what
launches each server_key's subprocess, kept apart from registry_ext.py
(what tools exist) and rcap_p.mcp_client (transport-agnostic plumbing).

Composes with voice_consent_gateway.deployment rather than duplicating it: Phase 3/4's
servers are launched exactly the way Phase 4 already launches them; this
module only adds the three new Phase 5 server_keys.

Test-harness honesty note (see mcp_servers/laptop_agent/server.py and
mcp_servers/remote_control/server.py's own docstrings for the fuller
version): build plan Section 7.1 describes laptop_agent as its own
Tailscale-reachable FastAPI service running on the physical laptop, and
remote_control as living on an always-on box sharing the laptop's home
LAN segment. Locally-spawned stdio subprocesses, as returned here, are
this project's test-harness stand-in for that real network topology —
the same category of simplification as Phase 2 running what would
eventually be a separate mini-PC's Ollama process locally, stated here
rather than silently assumed away.
"""
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from voice_consent_gateway.deployment import build_default_server_commands as build_phase4_default_commands
from voice_consent_gateway.deployment import build_demo_server_commands as build_phase4_demo_commands


def _phase5_servers_dir() -> Path:
    # mcp_servers/ is a sibling of device_control_gateway/ within THIS package's project root
    return Path(__file__).resolve().parent.parent / "mcp_servers"


def _phase5_server_commands() -> Dict[str, Tuple[str, List[str]]]:
    python = sys.executable
    servers_dir = _phase5_servers_dir()
    return {
        "laptop_agent": (python, [str(servers_dir / "laptop_agent" / "server.py")]),
        "remote_control": (python, [str(servers_dir / "remote_control" / "server.py")]),
        "home_assistant": (python, [str(servers_dir / "home_assistant" / "server.py")]),
    }


def build_default_server_commands(rcap_p_project_root: Path) -> Dict[str, Tuple[str, List[str]]]:
    """rcap_p_project_root is threaded through unchanged to voice_consent_gateway/rcap_p's
    own builders so their servers still resolve correctly even when Phase
    5 lives in a separate directory tree from Phase 3."""
    commands = dict(build_phase4_default_commands(rcap_p_project_root))
    commands.update(_phase5_server_commands())
    return commands


def build_demo_server_commands(rcap_p_project_root: Path) -> Dict[str, Tuple[str, List[str]]]:
    commands = dict(build_phase4_demo_commands(rcap_p_project_root))
    commands.update(_phase5_server_commands())
    return commands
