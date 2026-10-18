import shutil
import tempfile
from typing import Any, Dict

import pytest

from rcap_p.config import Config as RcapConfig
from rcap_p.ledger_bridge import Ledger as RcapLedger
from rcap_p.gateway import RCAPGateway

from voice_consent_gateway.config import Config
from voice_consent_gateway.consent import ConsentRegistry
from voice_consent_gateway.gateway import Phase4Gateway
from voice_consent_gateway.ledger_bridge import Ledger
from voice_consent_gateway.memory.gated_writer import GatedMemoryWriter
from voice_consent_gateway.memory.store import MemoryStore
from voice_consent_gateway.models import ConfirmationChannel, WriteCallRequest
from voice_consent_gateway.registry_ext import build_demo_registry


@pytest.fixture()
def tmp_data_dir():
    d = tempfile.mkdtemp(prefix="phase4_test_")
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
    """Same shape as rcap_p's own test double (tests/conftest.py) — kept
    as a separate class here rather than imported, since importing a
    module named `tests` across packages is fragile; the contract (an
    async callable with .calls/.set_response) is what matters, not sharing
    the exact class object."""

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
def rcap_gateway(tmp_data_dir, registry, ledger, fake_executor):
    # A separate rcap_p.config.Config (own data_dir) but wired to the SAME
    # ledger instance as everything else in the fixture graph — matches
    # how a real deployment shares one ledger across Phase 3 and Phase 4.
    rcap_config = RcapConfig(data_dir=tmp_data_dir)
    return RCAPGateway(config=rcap_config, registry=registry, ledger=ledger, tool_executor=fake_executor)


@pytest.fixture()
def consent_registry(config, ledger):
    return ConsentRegistry(config.consent_db_path, ledger)


@pytest.fixture()
def phase4_gateway(rcap_gateway, registry, consent_registry, ledger):
    return Phase4Gateway(rcap_gateway, registry, consent_registry, ledger)


@pytest.fixture()
def memory_store(config):
    return MemoryStore(config.memory_db_path)


@pytest.fixture()
def gated_memory(rcap_gateway, memory_store, ledger):
    return GatedMemoryWriter(rcap_gateway, memory_store, ledger)


def make_request(tool_name: str, arguments: Dict[str, Any], *,
                  channel: ConfirmationChannel = ConfirmationChannel.TEXT_TYPED,
                  session_id: str = "sess-1", explanation: str = "test explanation",
                  rotate_token=None, source_label: str = "test") -> WriteCallRequest:
    return WriteCallRequest(
        tool_name=tool_name, arguments=arguments, session_id=session_id,
        confirmation_channel=channel, source_label=source_label,
        explanation=explanation, rotate_token=rotate_token,
    )


async def fully_authorize_and_call(gateway: Phase4Gateway, rcap_gateway: RCAPGateway,
                                    consent_registry: ConsentRegistry, tool_name: str,
                                    arguments: Dict[str, Any], *,
                                    channel: ConfirmationChannel = ConfirmationChannel.TEXT_TYPED,
                                    session_id: str = "sess-1", explanation: str = "test explanation"):
    """Test convenience: grants consent + a ROTATE token, then calls —
    the "everything says yes" path most gate-specific tests want to start
    from before flipping exactly one thing off."""
    consent_registry.grant(tool_name, granted_by="test_fixture")
    token = rcap_gateway.grant_tool_permission(session_id, tool_name)
    req = make_request(tool_name, arguments, channel=channel, session_id=session_id,
                        explanation=explanation, rotate_token=token)
    return await gateway.call_tool(req)
