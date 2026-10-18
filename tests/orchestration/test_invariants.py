"""Tripwire tests (plan s9.2): structural checks over the source tree. They are NOT proof — the real
control is the capability verifier (O0b) — but they make an accidental violation loud."""
import ast
import os

import eternal_core
import orchestration
from orchestration import broker as broker_mod

ORCH = os.path.dirname(orchestration.__file__)
FORBIDDEN_IMPORT_ROOTS = {"voice_consent_gateway", "device_control_gateway", "rcap_p", "qap_p", "trading_risk_engine",
                          "mcp", "subprocess", "importlib", "socket", "requests", "httpx", "ollama", "anthropic", "openai"}
HTTP_ROOTS = {"requests", "httpx", "ollama", "anthropic", "openai", "socket"}
PROVIDERS_DIR = os.path.join(ORCH, "providers")
FORBIDDEN_CALLS = {"eval", "exec", "__import__", "compile"}


def _py_files(root):
    for d, _, fs in os.walk(root):
        if "__pycache__" in d:
            continue
        for f in fs:
            if f.endswith(".py"):
                yield os.path.join(d, f)


def _tree(path):
    return ast.parse(open(path, encoding="utf-8").read(), path)


def test_orchestration_imports_no_gateway_mcp_network_or_dynamic_import():
    for path in _py_files(ORCH):
        for n in ast.walk(_tree(path)):
            if isinstance(n, ast.Import):
                roots = {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                roots = {n.module.split(".")[0]}
            else:
                roots = set()
            banned = FORBIDDEN_IMPORT_ROOTS - (HTTP_ROOTS if path.startswith(PROVIDERS_DIR + os.sep) else set())
            assert not (roots & banned), f"{path} imports {roots & banned}"
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                assert n.func.id not in FORBIDDEN_CALLS, f"{path} calls {n.func.id}"


def test_only_broker_py_touches_a_gateway():                                      # I2
    for path in _py_files(ORCH):
        if os.path.basename(path) == "broker.py":
            continue
        for n in ast.walk(_tree(path)):
            idents = []
            if isinstance(n, ast.Attribute):
                idents.append(n.attr)
            elif isinstance(n, ast.Name):
                idents.append(n.id)
            elif isinstance(n, ast.arg):
                idents.append(n.arg)
            for i in idents:
                assert "gateway" not in i.lower() and i != "call_tool", f"{path}: references '{i}'"


def test_eternal_core_never_imports_orchestration():                              # I15
    core = os.path.dirname(eternal_core.__file__)
    for path in _py_files(core):
        for n in ast.walk(_tree(path)):
            if isinstance(n, ast.ImportFrom) and n.module:
                assert n.module.split(".")[0] != "orchestration", path
            if isinstance(n, ast.Import):
                assert all(a.name.split(".")[0] != "orchestration" for a in n.names), path


def test_broker_constants_match_the_real_gateway_vocabulary():                    # I3 drift guard
    from rcap_p.models import CallerKind
    from voice_consent_gateway.models import ConfirmationChannel
    assert broker_mod.CALLER_KIND == CallerKind.AUTOMATED_ROUTINE.value
    assert CallerKind.AUTOMATED_ROUTINE.value != CallerKind.OWNER_INTERACTIVE.value
    assert {"automated", "voice"} <= {c.value for c in ConfirmationChannel}


def test_agent_calls_are_never_fastlane_eligible_by_real_rcap_rule():            # I3
    src = open(os.path.join(os.path.dirname(__import__("rcap_p").__file__), "fastlane.py"), encoding="utf-8").read()
    assert "CallerKind.OWNER_INTERACTIVE" in src and "ctx.caller_kind != CallerKind.OWNER_INTERACTIVE" in src


def test_http_clients_are_confined_to_providers_package():                       # network boundary
    allowed = set()
    for path in _py_files(ORCH):
        for n in ast.walk(_tree(path)):
            if isinstance(n, ast.Import):
                roots = {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                roots = {n.module.split(".")[0]}
            else:
                continue
            if roots & HTTP_ROOTS:
                allowed.add(os.path.relpath(path, ORCH).split(os.sep)[0])
    assert allowed <= {"providers"}, allowed


def test_agent_logic_never_imports_providers():
    """Runtime, broker, and (later) agents see only the PolicyRouter, never a concrete provider."""
    for path in _py_files(ORCH):
        if path.startswith(PROVIDERS_DIR + os.sep) or os.path.relpath(path, ORCH).split(os.sep)[0] == "validation":
            continue
        for n in ast.walk(_tree(path)):
            mods = []
            if isinstance(n, ast.ImportFrom):
                mods = [(n.module or "")] + [a.name for a in n.names]
            elif isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            assert not any("providers" in m.split(".") for m in mods), f"{path} imports providers"
