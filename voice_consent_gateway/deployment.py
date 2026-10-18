"""
Deployment wiring for Phase 4 — same separation of concerns as
rcap_p/deployment.py (Phase 3): what launches each server_key's
subprocess, kept apart from registry_ext.py (what tools exist) and
rcap_p.mcp_client (transport-agnostic plumbing).

Composes with rcap_p.deployment rather than duplicating it: Phase 3's
servers (cinemavault, study_tracker, web_search[, malicious_test]) are
launched exactly the way Phase 3 already launches them; this module only
adds the two new Phase 4 server_keys.
"""
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from rcap_p.deployment import build_default_server_commands as build_rcap_p_default_commands
from rcap_p.deployment import build_demo_server_commands as build_rcap_p_demo_commands


def _phase4_servers_dir() -> Path:
    # mcp_servers/ is a sibling of voice_consent_gateway/ within THIS package's project root
    return Path(__file__).resolve().parent.parent / "mcp_servers"


def build_default_server_commands(rcap_p_project_root: Path) -> Dict[str, Tuple[str, List[str]]]:
    """rcap_p_project_root points at Phase 3's own project root (wherever
    rcap-p/ was unpacked/installed) so its servers still resolve correctly
    even when Phase 4 lives in a separate directory tree."""
    python = sys.executable
    commands = dict(build_rcap_p_default_commands(rcap_p_project_root))
    servers_dir = _phase4_servers_dir()
    commands["aether"] = (python, [str(servers_dir / "aether" / "server.py")])
    return commands


def build_demo_server_commands(rcap_p_project_root: Path) -> Dict[str, Tuple[str, List[str]]]:
    python = sys.executable
    commands = dict(build_rcap_p_demo_commands(rcap_p_project_root))
    servers_dir = _phase4_servers_dir()
    commands["aether"] = (python, [str(servers_dir / "aether" / "server.py")])
    commands["sensitive_demo"] = (python, [str(servers_dir / "sensitive_demo" / "server.py")])
    return commands
