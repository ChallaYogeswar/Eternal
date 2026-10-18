import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from rcap_p.config import Config
from rcap_p.ledger_bridge import Ledger
from rcap_p.gateway import RCAPGateway
from rcap_p.models import CallerKind, ToolCallContext
from rcap_p.registry import build_demo_registry


@pytest.fixture()
def tmp_data_dir():
    d = tempfile.mkdtemp(prefix="rcap_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def config(tmp_data_dir):
    return Config(data_dir=tmp_data_dir)


@pytest.fixture()
def ledger(config):
    return Ledger(config.ledger_db_path)


@pytest.fixture()
def registry():
    return build_demo_registry()


class FakeToolExecutor:
    """A minimal async tool_executor for gateway tests — no MCP subprocess
    involved, so ring-interaction tests stay fast and deterministic. The
    real subprocess path is proven separately in scripts/test_mcp_e2e.py."""

    def __init__(self, responses: Dict[str, Any] = None):
        self.responses = responses or {}
        self.calls = []

    async def __call__(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        self.calls.append((tool_name, arguments))
        if tool_name not in self.responses:
            raise KeyError(f"FakeToolExecutor has no canned response for '{tool_name}'")
        value = self.responses[tool_name]
        if isinstance(value, Exception):
            raise value
        return value

    def set_response(self, tool_name: str, value: Any) -> None:
        self.responses[tool_name] = value


@pytest.fixture()
def fake_executor():
    return FakeToolExecutor()


@pytest.fixture()
def gateway(config, registry, ledger, fake_executor):
    return RCAPGateway(
        config=config, registry=registry, ledger=ledger, tool_executor=fake_executor,
    )


def owner_ctx(session_id="sess-1", source_label="owner_direct") -> ToolCallContext:
    return ToolCallContext(session_id=session_id, caller_kind=CallerKind.OWNER_INTERACTIVE, source_label=source_label)


def automated_ctx(session_id="sess-auto", source_label="scheduled_job") -> ToolCallContext:
    return ToolCallContext(session_id=session_id, caller_kind=CallerKind.AUTOMATED_ROUTINE, source_label=source_label)
