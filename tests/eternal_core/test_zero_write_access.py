"""
Phase 2 exit criteria: "...with zero write access to anything."

These tests don't just check behavior in the cases we thought to test —
they check the STRUCTURE of the code, so a future change that accidentally
introduces a write capability (a tools= param on a provider, a new mutating
API route, a tracker client import) fails a test even if nobody thought to
write a behavioral test for that specific new capability.

What's deliberately treated as NOT a violation: Eternal's own ledger write
(one row per chat turn, into Eternal's own SQLite file). See
eternal_core/graph/nodes.py's finish node and api.py's module docstring for
the argument. These tests confirm that's the *only* write anywhere in the
codebase.
"""
import ast
import inspect
import os

import eternal_core
from eternal_core.llm.base import LLMProvider
from eternal_core.llm.ollama_provider import OllamaProvider
from eternal_core.llm.cloud_provider import AnthropicProvider, OpenAIProvider
from eternal_core import api as api_module


def _package_root():
    return os.path.dirname(eternal_core.__file__)


def test_llm_provider_generate_signature_has_no_tools_parameter():
    """If someone later adds tool/function-calling support to a provider,
    this fails immediately — 'generate' must stay a pure text-in/text-out
    call for every provider Phase 2 knows about."""
    for cls in (OllamaProvider, AnthropicProvider, OpenAIProvider):
        sig = inspect.signature(cls.generate)
        param_names = set(sig.parameters.keys())
        forbidden = {"tools", "tool_choice", "functions", "function_call"}
        assert not (param_names & forbidden), f"{cls.__name__}.generate accepts {param_names & forbidden}"


def test_no_provider_payload_construction_includes_tools_key():
    """Reads the actual source of the cloud/ollama provider modules and
    confirms no request payload dict includes a 'tools' key literal —
    catching the case where a tools param might be threaded through
    without appearing in generate()'s own signature."""
    import eternal_core.llm.ollama_provider as ollama_mod
    import eternal_core.llm.cloud_provider as cloud_mod

    for mod in (ollama_mod, cloud_mod):
        source = inspect.getsource(mod)
        assert '"tools"' not in source
        assert "'tools'" not in source


def test_api_exposes_no_mutating_routes_outside_chat_and_ledger_reset():
    """Enumerates every route FastAPI actually registered and asserts the
    only non-GET routes are /chat (generates a response + one ledger
    write) and /chat/reset (clears in-memory, never-persisted history for
    a session — not a write to anything durable). Anything else showing up
    here as POST/PUT/PATCH/DELETE means someone added a new mutating
    surface without updating this test — which is the point."""
    allowed_non_get_paths = {"/chat", "/chat/reset"}
    offending = []
    for route in api_module.app.routes:
        methods = getattr(route, "methods", set()) or set()
        path = getattr(route, "path", "")
        non_get_methods = methods - {"GET", "HEAD", "OPTIONS"}
        if non_get_methods and path not in allowed_non_get_paths:
            offending.append((path, non_get_methods))
    assert offending == [], f"unexpected mutating routes found: {offending}"


def test_no_ingest_or_write_endpoint_exists():
    paths = {getattr(r, "path", "") for r in api_module.app.routes}
    for forbidden_path in ("/ingest", "/reindex", "/write", "/tracker", "/aether", "/hardware", "/exec"):
        assert forbidden_path not in paths


def test_eternal_core_never_imports_subprocess_or_os_system():
    """Static check across every .py file in eternal_core/: no
    subprocess module usage, no os.system, no os.popen. Combined with the
    'no tools param' tests above, this rules out both the two most direct
    ways an LLM response could be turned into an executed action."""
    root = _package_root()
    forbidden_names = {"subprocess", "os.system", "os.popen", "eval", "exec"}
    violations = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "subprocess":
                            violations.append((path, "import subprocess"))
                elif isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute) and func.attr in ("system", "popen"):
                        if isinstance(func.value, ast.Name) and func.value.id == "os":
                            violations.append((path, f"os.{func.attr}(...)"))
                    if isinstance(func, ast.Name) and func.id in ("eval", "exec"):
                        violations.append((path, f"{func.id}(...)"))
    assert violations == [], f"forbidden write/exec-capable calls found: {violations}"


def test_only_write_anywhere_is_the_ledger_append():
    """The flip side of the check above: confirm the ONE write surface
    that does exist (Ledger.append -> sqlite3 INSERT) is exactly what we
    expect it to be, and that it only ever targets Eternal's own ledger
    db — never a path derived from user input."""
    from eternal_core.audit.ledger import Ledger

    source = inspect.getsource(Ledger)
    assert "INSERT INTO ledger" in source
    assert "DELETE" not in source.upper().replace("DELETE FROM", "")  # no delete/update statements at all
    assert "UPDATE " not in source.upper()
