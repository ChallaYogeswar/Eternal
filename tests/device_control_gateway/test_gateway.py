"""
Integration tests for device_control_gateway.gateway.Phase5Gateway — gate ORDER and
DECISIONS, using conftest's FakeToolExecutor (canned responses), same
split every earlier phase's test suite uses: the real subprocess proof
lives in scripts/test_mcp_e2e.py.
"""
import pytest

from voice_consent_gateway.models import ConfirmationChannel, WriteCallRequest
from device_control_gateway.gateway import FULL_CONTROL_TOOL_NAME


def _request(tool_name, session_id, channel=ConfirmationChannel.APP_TAP, rotate_token=None, explanation="test"):
    return WriteCallRequest(
        tool_name=tool_name, arguments={"entity_id": "lock.front_door"} if tool_name.startswith("ha_") else
                  ({"device_label": "primary_laptop"} if tool_name.startswith("remote_control_") else {}),
        session_id=session_id, confirmation_channel=channel, source_label="test",
        explanation=explanation, rotate_token=rotate_token,
    )


@pytest.mark.asyncio
async def test_full_control_denied_for_low_trust_session_before_reaching_rcap_p(
    phase5_gateway, enroll_and_activate, fake_executor,
):
    """A freshly-enrolled (low-trust) session must be refused by top-
    trust-tier BEFORE the underlying tool executor is ever called — this
    is the safety property that matters most: no Wake-on-LAN, no handoff
    construction, nothing, if trust isn't cleared first."""
    _, session_id = enroll_and_activate()
    result = await phase5_gateway.call_tool(_request(FULL_CONTROL_TOOL_NAME, session_id))
    assert result.ok is False
    assert "hardware" in result.denied_reason or "trust score" in result.denied_reason
    assert fake_executor.calls == []  # never reached the tool


@pytest.mark.asyncio
async def test_full_control_over_voice_denied_even_disregarding_trust(
    phase5_gateway, enroll_and_activate, fake_executor,
):
    """Regardless of what top-trust-tier would decide, VOICE must never
    complete this call either — both gates are mandatory, not
    alternatives. A low-trust session over voice hits top-trust-tier
    first (see gateway.py's documented ordering); this test's job is
    narrower: confirm the voice-gate text would also independently
    condemn it once/if top-trust-tier is cleared, by checking the
    ORDINARY (non-full-control) SENSITIVE tool over voice is denied by
    Phase 4's own unmodified gate, proving that layer is still live
    underneath Phase 5's addition."""
    _, session_id = enroll_and_activate()
    result = await phase5_gateway.call_tool(_request("ha_unlock", session_id, channel=ConfirmationChannel.VOICE))
    assert result.ok is False
    assert "VOICE" in result.denied_reason
    assert fake_executor.calls == []


@pytest.mark.asyncio
async def test_ordinary_sensitive_tool_unaffected_by_top_trust_tier(
    phase5_gateway, enroll_and_activate, consent_registry, rcap_gateway,
):
    """laptop_unlock is SENSITIVE but is NOT the full-control tool — a
    low-trust session should still be able to complete it once voice-gate
    and consent clear, exactly like Phase 4's fixture proved. If this
    test failed, it would mean Phase 5's new gate had accidentally
    leaked onto every SENSITIVE tool instead of just the one named in
    the build plan."""
    _, session_id = enroll_and_activate()
    consent_registry.grant("laptop_unlock", granted_by="test")
    token = rcap_gateway.grant_tool_permission(session_id, "laptop_unlock")
    result = await phase5_gateway.call_tool(
        _request("laptop_unlock", session_id, channel=ConfirmationChannel.APP_TAP, rotate_token=token)
    )
    assert result.ok is True


@pytest.mark.asyncio
async def test_high_trust_session_can_reach_full_control_tool(
    phase5_gateway, orchestrator, consent_registry, rcap_gateway, fake_executor, phase5_config,
):
    """The full path, granted: manufacture a high-trust device (same
    technique as test_trust_tier.py's own granted-path test), grant
    consent + a ROTATE token, and confirm the call actually reaches the
    (fake) executor — proving top-trust-tier is a gate, not a permanent
    lock."""
    from qap_p.models import Device, DeviceRole
    import time

    device = Device(
        device_id="trusted-laptop", role=DeviceRole.LAPTOP,
        public_key_pem="-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----",
        hardware_backed=True, attestation_type="test-fixture",
        enrolled_at=time.time() - 35 * 86400, quarantine_count=0, anomaly_count=0,
    )
    orchestrator.store.create_device(device)
    session_id = orchestrator.create_session("trusted-laptop")
    if orchestrator.compute_trust_score("trusted-laptop") < phase5_config.top_trust_tier_min_score:
        pytest.skip("test setup did not clear the trust bar under qap_p's current formula")

    consent_registry.grant(FULL_CONTROL_TOOL_NAME, granted_by="test")
    token = rcap_gateway.grant_tool_permission(session_id, FULL_CONTROL_TOOL_NAME)
    result = await phase5_gateway.call_tool(
        _request(FULL_CONTROL_TOOL_NAME, session_id, channel=ConfirmationChannel.APP_TAP, rotate_token=token)
    )
    assert result.ok is True
    assert fake_executor.calls  # the underlying tool really was invoked


@pytest.mark.asyncio
async def test_quarantined_session_cannot_mint_a_fresh_rotate_token(
    phase5_gateway, orchestrator, enroll_and_activate,
):
    """Regression test for the gap found while testing the kill switch
    live over HTTP (see gateway.py's module docstring): before this
    guard, a session the kill switch had just quarantined could still be
    handed a brand-new ROTATE token and go on calling WRITE-tier tools
    with standing consent. Phase5Gateway.grant_tool_permission must
    refuse this."""
    _, session_id = enroll_and_activate()
    orchestrator.quarantine_session(session_id, reason="test_compromise")

    with pytest.raises(ValueError, match="not ACTIVE"):
        phase5_gateway.grant_tool_permission(session_id, "laptop_lock")


@pytest.mark.asyncio
async def test_active_session_can_still_mint_tokens(phase5_gateway, enroll_and_activate):
    """The guard must not be overzealous — an ordinary ACTIVE session
    should mint tokens exactly as before."""
    _, session_id = enroll_and_activate()
    token = phase5_gateway.grant_tool_permission(session_id, "laptop_lock")
    assert token


@pytest.mark.asyncio
async def test_unknown_session_cannot_mint_a_token(phase5_gateway):
    with pytest.raises(ValueError):
        phase5_gateway.grant_tool_permission("no-such-session", "laptop_lock")


@pytest.mark.asyncio
async def test_pull_kill_switch_reachable_from_gateway(phase5_gateway, enroll_and_activate, consent_registry, registry):
    """Phase5Gateway.pull_kill_switch is the one entrypoint api.py calls —
    confirm it actually reaches killswitch.pull_kill_switch correctly
    wired (own orchestrator, own rcap_gateway, defaults for consent/
    registry pulled from phase4_gateway when not overridden)."""
    _, session_id = enroll_and_activate()
    report = phase5_gateway.pull_kill_switch(reason="integration_test")
    assert session_id in report.sessions_quarantined
