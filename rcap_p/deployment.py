"""
Deployment wiring — maps each registry server_key to the actual command
that launches its MCP server subprocess. Kept as its own tiny module,
separate from registry.py (which is about *what tools exist and their
sensitivity*, not *how to launch the process behind them*) and separate
from mcp_client.py (which is transport-agnostic plumbing that doesn't need
to know real file paths to be testable).

This is the one place a real deployment's file layout leaks in — if
mcp_servers/ ever moves, or a server gains a second entrypoint flag, this
is the only file that needs to change.
"""
import sys
from pathlib import Path
from typing import Dict, List, Tuple


def build_default_server_commands(project_root: Path) -> Dict[str, Tuple[str, List[str]]]:
    python = sys.executable
    servers_dir = project_root / "mcp_servers"
    return {
        "cinemavault": (python, [str(servers_dir / "cinemavault" / "server.py")]),
        "study_tracker": (python, [str(servers_dir / "study_tracker" / "server.py")]),
        "web_search": (python, [str(servers_dir / "web_search" / "server.py")]),
    }


def build_demo_server_commands(project_root: Path) -> Dict[str, Tuple[str, List[str]]]:
    """Adds the malicious_test fixture server on top of the real set —
    demo/test use only, mirroring registry.build_demo_registry()."""
    commands = build_default_server_commands(project_root)
    servers_dir = project_root / "mcp_servers"
    commands["malicious_test"] = (sys.executable, [str(servers_dir / "malicious_test" / "server.py")])
    return commands
