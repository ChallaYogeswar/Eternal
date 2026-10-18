import os
import sys
import tempfile
import shutil

import pytest

# Ensure mcp_servers/*/data.py modules (self-contained, no package __init__
# needed for import since each dir has one) are importable by dotted path
# for direct unit testing, same convenience Phase 4's tests use for
# mcp_servers.aether.data.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture()
def tmp_data_dir():
    d = tempfile.mkdtemp(prefix="phase5_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def laptop_provider(tmp_data_dir):
    from mcp_servers.laptop_agent.data import SimulatedLaptopActionProvider
    # isolated state_path per test — SimulatedLaptopActionProvider persists
    # to disk now (see its class docstring for why), so tests must not
    # share the module's default file or they'd pollute each other.
    return SimulatedLaptopActionProvider(state_path=os.path.join(tmp_data_dir, "laptop_state.json"))


@pytest.fixture()
def ha_client():
    from mcp_servers.home_assistant.data import FixtureHomeAssistantClient
    return FixtureHomeAssistantClient()


@pytest.fixture()
def orchestrator(tmp_data_dir):
    from qap_p.orchestrator import Orchestrator
    from qap_p.config import Config as QapConfig
    return Orchestrator(QapConfig(data_dir=tmp_data_dir))


@pytest.fixture()
def trust_lookup(orchestrator):
    from device_control_gateway.trust_bridge import QapPTrustLookup
    return QapPTrustLookup(orchestrator)


@pytest.fixture()
def phase5_config(tmp_data_dir):
    from device_control_gateway.config import Config
    return Config(data_dir=tmp_data_dir)


@pytest.fixture()
def ledger(tmp_data_dir):
    from device_control_gateway.ledger_bridge import Ledger
    return Ledger(os.path.join(tmp_data_dir, "ledger.db"))


class FakeToolExecutor:
    """Same role as voice_consent_gateway/rcap-p's own FakeToolExecutor fixtures: a
    canned-response async callable so gateway-level tests exercise gate
    ORDER and DECISIONS without needing real MCP subprocesses — the real
    subprocess proof lives in scripts/test_mcp_e2e.py, same split every
    earlier phase's test suite uses."""

    def __init__(self):
        self.calls = []

    async def __call__(self, tool_name: str, arguments: dict):
        self.calls.append((tool_name, arguments))
        if tool_name == "remote_control_request_full_session":
            return {"wake_on_lan_sent": True, "target_mac_address": "00:11:22:33:44:55",
                     "protocol": "rustdesk", "connection_hint": "test-hint", "session_id": "fcs-test", "expires_at": 0.0, "note": ""}
        if tool_name in ("ha_lock", "ha_unlock"):
            return {"entity_id": arguments.get("entity_id"), "domain": "lock",
                     "state": "locked" if tool_name == "ha_lock" else "unlocked", "attributes": {}}
        if tool_name.startswith("laptop_"):
            return {"ok": True, "action": tool_name, "detail": "fake", "power_state": "awake", "lock_state": "unlocked"}
        return {"ok": True}


@pytest.fixture()
def fake_executor():
    return FakeToolExecutor()


@pytest.fixture()
def registry():
    from device_control_gateway.registry_ext import build_demo_registry
    return build_demo_registry()


@pytest.fixture()
def rcap_gateway(phase5_config, registry, ledger, fake_executor, trust_lookup):
    from rcap_p.config import Config as RcapConfig
    from rcap_p.gateway import RCAPGateway
    return RCAPGateway(RcapConfig(data_dir=phase5_config.data_dir), registry, ledger, fake_executor,
                        trust_lookup=trust_lookup)


@pytest.fixture()
def consent_registry(phase5_config, ledger):
    from voice_consent_gateway.consent import ConsentRegistry
    return ConsentRegistry(os.path.join(phase5_config.data_dir, "consent.db"), ledger)


@pytest.fixture()
def phase4_gateway(rcap_gateway, registry, consent_registry, ledger):
    from voice_consent_gateway.gateway import Phase4Gateway
    return Phase4Gateway(rcap_gateway, registry, consent_registry, ledger)


@pytest.fixture()
def top_trust_tier_gate(phase5_config, trust_lookup):
    from device_control_gateway.trust_tier import TopTrustTierGate
    return TopTrustTierGate(phase5_config, trust_lookup)


@pytest.fixture()
def phase5_gateway(phase4_gateway, top_trust_tier_gate, orchestrator, ledger):
    from device_control_gateway.gateway import Phase5Gateway
    return Phase5Gateway(phase4_gateway, top_trust_tier_gate, orchestrator, ledger)


@pytest.fixture()
def enroll_and_activate(orchestrator):
    """Factory fixture: enrolls a device and creates a session, returning
    (device, session_id) — every gateway-integration test needs this, so
    it lives here rather than being copy-pasted per test. A factory
    (callable) rather than a plain function so it stays reachable as a
    normal pytest fixture without needing `tests` to be an importable
    package."""
    from qap_p.models import DeviceRole

    def _make(device_id="dev-1", role_str="laptop"):
        device = orchestrator.enroll_device(device_id, DeviceRole(role_str))
        session_id = orchestrator.create_session(device_id)
        return device, session_id

    return _make
