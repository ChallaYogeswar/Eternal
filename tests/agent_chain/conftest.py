"""The REAL chain, as deployed: QAP-P orchestrator + RCAP + Phase 4 (consent) + Phase 5 (kill switch / ACTIVE guard)
+ Phase 6 (trading gates), with only the MCP executor faked. The capability layer is wrapped around Phase 6 exactly as
`agent_chain` does it in production."""
import os
import shutil
import tempfile

import pytest

from agent_chain.gateway import CapabilityCoreGateway, CapabilityGateway
from agent_chain.port import ChainCapabilityPort, tier_lookup_from_registry
from orchestration.capability import CapabilityIssuer, CapabilityVerifier, generate_keypair
from orchestration.consent import ConsentStore


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeExecutor:
    def __init__(self):
        self.calls = []

    async def __call__(self, tool_name, arguments):
        self.calls.append((tool_name, dict(arguments)))
        if tool_name == "web_search":
            return {"query": arguments.get("query", ""), "results": []}
        if tool_name == "ha_get_state":
            return {"entity_id": arguments.get("entity_id", "light.x"), "domain": "light", "state": "on", "attributes": {}}
        if tool_name.startswith("laptop_"):
            return {"ok": True, "action": tool_name, "detail": "fake", "power_state": "awake", "lock_state": "unlocked"}
        return {"ok": True}


class _TrustLookup:
    pass


@pytest.fixture()
def data_dir():
    d = tempfile.mkdtemp(prefix="agent_chain_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def real(data_dir):
    from device_control_gateway.config import Config as P5Config
    from device_control_gateway.gateway import Phase5Gateway
    from device_control_gateway.ledger_bridge import Ledger
    from device_control_gateway.registry_ext import build_demo_registry
    from device_control_gateway.trust_bridge import QapPTrustLookup
    from device_control_gateway.trust_tier import TopTrustTierGate
    from qap_p.config import Config as QapConfig
    from qap_p.models import DeviceRole
    from qap_p.orchestrator import Orchestrator
    from rcap_p.config import Config as RcapConfig
    from rcap_p.gateway import RCAPGateway
    from trading_risk_engine.config import Config as P6Config
    from trading_risk_engine.gateway import Phase6Gateway
    from trading_risk_engine.ledger_bridge import Ledger as P6Ledger
    from trading_risk_engine.regulatory import DailyReauthGate, LiveTradingGate, OrderRateLimiter, StaticIPHostGate
    from voice_consent_gateway.consent import ConsentRegistry
    from voice_consent_gateway.gateway import Phase4Gateway

    class Reauthed:
        def token_status(self):
            from trading_risk_engine.broker.daily_reauth import TokenStatus
            return TokenStatus(has_token=True, obtained_at_ist="09:00", valid_until_ist="tomorrow 07:30",
                               is_valid_now=True, hours_remaining=20.0)

    orch = Orchestrator(QapConfig(data_dir=data_dir))
    trust = QapPTrustLookup(orch)
    p5cfg = P5Config(data_dir=data_dir)
    registry = build_demo_registry()
    ledger = Ledger(os.path.join(data_dir, "ledger.db"))
    executor = FakeExecutor()
    rcap = RCAPGateway(RcapConfig(data_dir=data_dir), registry, ledger, executor, trust_lookup=trust)
    consent_registry = ConsentRegistry(os.path.join(data_dir, "consent.db"), ledger)
    p4 = Phase4Gateway(rcap, registry, consent_registry, ledger)
    p5 = Phase5Gateway(p4, TopTrustTierGate(p5cfg, trust), orch, ledger)
    p6cfg = P6Config(live_trading_enabled=False, data_dir=data_dir)
    p6 = Phase6Gateway(p5, TopTrustTierGate(p5cfg, trust),
                       LiveTradingGate(p6cfg, static_ip_gate=StaticIPHostGate(p6cfg),
                                       daily_reauth_gate=DailyReauthGate(Reauthed()),
                                       rate_limiter=OrderRateLimiter(100.0)),
                       P6Ledger(p6cfg.ledger_db_path))

    def new_session(device_id="dev-1"):
        orch.enroll_device(device_id, DeviceRole("laptop"))
        return orch.create_session(device_id)

    class Real:
        pass
    r = Real()
    r.orch, r.registry, r.executor, r.p4, r.p5, r.p6, r.ledger = orch, registry, executor, p4, p5, p6, ledger
    r.consent_registry, r.new_session = consent_registry, new_session
    return r


class Wired:
    pass


@pytest.fixture()
def wired(real):
    clock = Clock()
    priv, pub = generate_keypair()
    ident_priv, ident_pub = generate_keypair()
    verifier = CapabilityVerifier(pub, tier_lookup=tier_lookup_from_registry(real.registry), clock=clock)
    issuer = CapabilityIssuer(priv, clock=clock)
    gw = CapabilityGateway(real.p6, verifier, ledger=real.ledger, identity_key=ident_priv,
                           identity_tools=frozenset({"ha_get_state"}), clock=clock)
    consent = ConsentStore(clock=clock)
    port = ChainCapabilityPort(issuer, verifier, gw.grant_tool_permission, consent=consent)
    w = Wired()
    w.clock, w.verifier, w.issuer, w.gateway, w.core = clock, verifier, issuer, gw, CapabilityCoreGateway(gw)
    w.port, w.consent, w.ident_pub, w.real = port, consent, ident_pub, real
    w.pub = pub
    return w
