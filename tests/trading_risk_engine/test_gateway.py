from typing import Optional

import pytest

from voice_consent_gateway.models import ConfirmationChannel, WriteCallRequest, WriteCallResult
from trading_risk_engine.config import Config
from trading_risk_engine.gateway import LIVE_ORDER_TOOL_NAME, Phase6Gateway
from trading_risk_engine.ledger_bridge import Ledger
from trading_risk_engine.regulatory import DailyReauthGate, LiveTradingGate, OrderRateLimiter, StaticIPHostGate
from device_control_gateway.trust_tier import TopTrustTierGate
from rcap_p.fastlane import TrustLookup


class _FakeTrustLookup(TrustLookup):
    def __init__(self, state="ACTIVE", device_id="trading-host", hardware_backed=True, score=0.90):
        self.state = state
        self.device_id = device_id
        self.hardware_backed = hardware_backed
        self.score = score

    def get_session_state(self, session_id: str) -> Optional[str]:
        return self.state

    def get_device_id_for_session(self, session_id: str) -> Optional[str]:
        return self.device_id

    def is_hardware_backed(self, device_id: str) -> bool:
        return self.hardware_backed

    def compute_trust_score(self, device_id: str) -> float:
        return self.score


class _AlwaysReauthedManager:
    def token_status(self):
        from trading_risk_engine.broker.daily_reauth import TokenStatus
        return TokenStatus(has_token=True, obtained_at_ist="09:00", valid_until_ist="tomorrow 07:30",
                           is_valid_now=True, hours_remaining=20.0)


class _FakePhase5Gateway:
    """Records whether it was reached at all — Phase6Gateway's job is to
    decide whether to reach it, not to re-test Phase 5's own internals
    (already covered by Phase 5's own extensive test suite)."""

    def __init__(self):
        self.calls = []

    async def call_tool(self, request: WriteCallRequest) -> WriteCallResult:
        self.calls.append(request)
        return WriteCallResult(tool_name=request.tool_name, ok=True, content={"stub": "reached device_control_gateway"},
                               denied_reason=None, underlying_result=None, ledger_entry_hashes=["stub-hash"])

    def grant_tool_permission(self, session_id, tool_name):
        return "stub-token"


def _config(**overrides) -> Config:
    defaults = dict(
        live_trading_enabled=True, trading_host_role="home-server-fixed-ip",
        max_orders_per_second=100.0, data_dir="/tmp/phase6_gateway_test_data",
    )
    defaults.update(overrides)
    return Config(**defaults)


def _make_gateway(config: Config, trust_lookup: TrustLookup, phase5_gateway=None):
    ledger = Ledger(config.ledger_db_path)
    top_trust_gate = TopTrustTierGate(config, trust_lookup=trust_lookup)
    live_trading_gate = LiveTradingGate(
        config,
        static_ip_gate=StaticIPHostGate(config),
        daily_reauth_gate=DailyReauthGate(_AlwaysReauthedManager()),
        rate_limiter=OrderRateLimiter(config.max_orders_per_second),
    )
    phase5_gateway = phase5_gateway or _FakePhase5Gateway()
    return Phase6Gateway(phase5_gateway, top_trust_gate, live_trading_gate, ledger), phase5_gateway


def _live_order_request(**arg_overrides) -> WriteCallRequest:
    args = {"symbol": "RELIANCE.NS", "side": "BUY", "quantity": 5, "order_type": "LIMIT", "limit_price": 2500.0}
    args.update(arg_overrides)
    return WriteCallRequest(
        tool_name=LIVE_ORDER_TOOL_NAME, arguments=args, session_id="session-1",
        confirmation_channel=ConfirmationChannel.APP_TAP, source_label="test", explanation="test live order",
    )


@pytest.mark.asyncio
async def test_live_order_reaches_phase5_when_every_gate_passes(tmp_path):
    config = _config(data_dir=str(tmp_path))
    gateway, fake_phase5 = _make_gateway(config, _FakeTrustLookup())
    result = await gateway.call_tool(_live_order_request())
    assert result.ok is True
    assert len(fake_phase5.calls) == 1  # actually reached Phase 5's gateway


@pytest.mark.asyncio
async def test_live_order_denied_below_top_trust_tier_never_reaches_phase5(tmp_path):
    config = _config(data_dir=str(tmp_path))
    gateway, fake_phase5 = _make_gateway(config, _FakeTrustLookup(score=0.50))  # below 0.85 default
    result = await gateway.call_tool(_live_order_request())
    assert result.ok is False
    assert "trust score" in result.denied_reason
    assert len(fake_phase5.calls) == 0  # never reached — this is the whole point of the gate


@pytest.mark.asyncio
async def test_live_order_denied_for_quarantined_session_never_reaches_phase5(tmp_path):
    config = _config(data_dir=str(tmp_path))
    gateway, fake_phase5 = _make_gateway(config, _FakeTrustLookup(state="QUARANTINE"))
    result = await gateway.call_tool(_live_order_request())
    assert result.ok is False
    assert "QUARANTINE" in result.denied_reason or "not ACTIVE" in result.denied_reason
    assert len(fake_phase5.calls) == 0


@pytest.mark.asyncio
async def test_live_order_denied_when_live_trading_disabled(tmp_path):
    config = _config(data_dir=str(tmp_path), live_trading_enabled=False)
    gateway, fake_phase5 = _make_gateway(config, _FakeTrustLookup())
    result = await gateway.call_tool(_live_order_request())
    assert result.ok is False
    assert "disabled" in result.denied_reason
    assert len(fake_phase5.calls) == 0


@pytest.mark.asyncio
async def test_live_order_denied_without_static_ip_host_role(tmp_path):
    config = _config(data_dir=str(tmp_path), trading_host_role="")  # not the fixed-IP host
    gateway, fake_phase5 = _make_gateway(config, _FakeTrustLookup())
    result = await gateway.call_tool(_live_order_request())
    assert result.ok is False
    assert len(fake_phase5.calls) == 0


@pytest.mark.asyncio
async def test_live_order_denied_for_unprotected_market_order(tmp_path):
    config = _config(data_dir=str(tmp_path))
    gateway, fake_phase5 = _make_gateway(config, _FakeTrustLookup())
    result = await gateway.call_tool(_live_order_request(order_type="MARKET", market_protection_pct=None))
    assert result.ok is False
    assert len(fake_phase5.calls) == 0


@pytest.mark.asyncio
async def test_non_live_order_tools_bypass_all_phase6_checks_entirely(tmp_path):
    # A tool Phase6Gateway doesn't specially handle at all (e.g. a paper
    # order) must pass straight through, even with a hopeless trust score
    # — Phase6Gateway only adds friction for the ONE tool that needs it.
    config = _config(data_dir=str(tmp_path))
    gateway, fake_phase5 = _make_gateway(config, _FakeTrustLookup(score=0.0))
    request = WriteCallRequest(
        tool_name="trading_place_paper_order", arguments={"symbol": "TCS.NS", "side": "BUY", "quantity": 1},
        session_id="session-1", confirmation_channel=ConfirmationChannel.VOICE, source_label="test",
        explanation="paper order",
    )
    result = await gateway.call_tool(request)
    assert result.ok is True
    assert len(fake_phase5.calls) == 1


@pytest.mark.asyncio
async def test_grant_tool_permission_is_a_pure_passthrough(tmp_path):
    config = _config(data_dir=str(tmp_path))
    gateway, fake_phase5 = _make_gateway(config, _FakeTrustLookup())
    token = gateway.grant_tool_permission("session-1", LIVE_ORDER_TOOL_NAME)
    assert token == "stub-token"
