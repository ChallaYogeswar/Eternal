import pytest

from rcap_p.models import ToolSensitivity

from voice_consent_gateway.models import ConfirmationChannel
from .conftest import fully_authorize_and_call, make_request


@pytest.fixture(autouse=True)
def _canned_responses(fake_executor):
    fake_executor.set_response("aether_add_expense", {
        "id": "e1", "name": "lunch", "amount": 200, "currency": "INR", "category": "other", "date": "2026-08-05",
    })
    fake_executor.set_response("sensitive_demo_unlock_door", "[FIXTURE] unlocked front")
    fake_executor.set_response("cinemavault_list_movies", {"result": []})


async def test_unknown_tool_is_a_clean_denial_not_a_crash(phase4_gateway):
    req = make_request("no_such_tool", {})
    result = await phase4_gateway.call_tool(req)
    assert result.ok is False
    assert "unknown tool" in result.denied_reason


async def test_write_tool_denied_without_consent(phase4_gateway):
    req = make_request("aether_add_expense", {"name": "lunch", "amount": 200},
                        channel=ConfirmationChannel.VOICE)
    result = await phase4_gateway.call_tool(req)
    assert result.ok is False
    assert "consent" in result.denied_reason.lower()


async def test_write_tool_denied_with_consent_but_no_rotate_token(phase4_gateway, consent_registry):
    consent_registry.grant("aether_add_expense", granted_by="test")
    req = make_request("aether_add_expense", {"name": "lunch", "amount": 200})
    result = await phase4_gateway.call_tool(req)
    assert result.ok is False  # RCAP-P's own ROTATE ring still applies, unchanged


async def test_write_tool_succeeds_with_consent_and_rotate_token(phase4_gateway, rcap_gateway, consent_registry):
    result = await fully_authorize_and_call(
        phase4_gateway, rcap_gateway, consent_registry, "aether_add_expense", {"name": "lunch", "amount": 200},
    )
    assert result.ok is True
    assert result.content["amount"] == 200


async def test_write_tool_over_voice_succeeds_when_fully_authorized(phase4_gateway, rcap_gateway, consent_registry):
    """WRITE tier (unlike SENSITIVE) is voice-triggerable once consent +
    ROTATE token are both in place — this is the exit criterion's first
    clause ("a spoken command changes tracker data") having real teeth."""
    result = await fully_authorize_and_call(
        phase4_gateway, rcap_gateway, consent_registry, "aether_add_expense", {"name": "lunch", "amount": 200},
        channel=ConfirmationChannel.VOICE,
    )
    assert result.ok is True


async def test_sensitive_tool_denied_over_voice_even_when_fully_authorized(phase4_gateway, rcap_gateway, consent_registry):
    """The exit criterion's second clause. Consent AND a ROTATE token are
    both granted — everything RCAP-P and consent would need to say yes is
    present — and it is STILL denied, because the voice gate runs first
    and does not consult either of those signals."""
    result = await fully_authorize_and_call(
        phase4_gateway, rcap_gateway, consent_registry, "sensitive_demo_unlock_door", {"door": "front"},
        channel=ConfirmationChannel.VOICE,
    )
    assert result.ok is False
    assert "SENSITIVE-tier" in result.denied_reason
    assert "VOICE" in result.denied_reason


async def test_sensitive_tool_succeeds_over_app_tap_when_fully_authorized(phase4_gateway, rcap_gateway, consent_registry):
    result = await fully_authorize_and_call(
        phase4_gateway, rcap_gateway, consent_registry, "sensitive_demo_unlock_door", {"door": "front"},
        channel=ConfirmationChannel.APP_TAP,
    )
    assert result.ok is True


async def test_voice_gate_runs_before_consent_check(phase4_gateway):
    """Even with ZERO consent granted, a SENSITIVE-tier voice call must be
    denied for the voice-gate reason specifically, not the consent
    reason — proving the check order in gateway.py's docstring."""
    req = make_request("sensitive_demo_unlock_door", {"door": "front"}, channel=ConfirmationChannel.VOICE)
    result = await phase4_gateway.call_tool(req)
    assert result.ok is False
    assert "SENSITIVE-tier" in result.denied_reason  # not "requires consent"


async def test_read_only_phase3_tools_still_work_through_phase4_gateway(phase4_gateway, rcap_gateway, consent_registry):
    """READ_ONLY tools (Phase 3's own) need no consent at all — Phase 4's
    consent gate only applies to WRITE/SENSITIVE — and Phase 3's FASTLANE/
    ROTATE/etc. all still apply exactly as before."""
    token = rcap_gateway.grant_tool_permission("sess-1", "cinemavault_list_movies")
    req = make_request("cinemavault_list_movies", {"limit": 5, "offset": 0}, rotate_token=token)
    result = await phase4_gateway.call_tool(req)
    assert result.ok is True


async def test_explanation_is_always_ledger_logged_on_success_and_denial(phase4_gateway, rcap_gateway, consent_registry, ledger):
    result = await fully_authorize_and_call(
        phase4_gateway, rcap_gateway, consent_registry, "aether_add_expense", {"name": "lunch", "amount": 200},
        explanation="You said: 'add expense two hundred for lunch' — logging it.",
    )
    assert result.ok is True
    recent = ledger.recent(5)
    explained = [e for e in recent if e["event_type"] == "phase4_write_call_explained"]
    assert len(explained) == 1
    assert explained[0]["payload"]["explanation"] == "You said: 'add expense two hundred for lunch' — logging it."
    assert explained[0]["payload"]["ok"] is True

    denial_req = make_request("sensitive_demo_unlock_door", {"door": "front"}, channel=ConfirmationChannel.VOICE,
                               explanation="You said: 'unlock the front door'.")
    await phase4_gateway.call_tool(denial_req)
    recent2 = ledger.recent(5)
    denied = [e for e in recent2 if e["event_type"] == "phase4_voice_gate_denied"]
    assert len(denied) == 1
    assert denied[0]["payload"]["explanation"] == "You said: 'unlock the front door'."


async def test_ledger_chain_stays_intact_through_a_mixed_session(phase4_gateway, rcap_gateway, consent_registry, ledger):
    await fully_authorize_and_call(phase4_gateway, rcap_gateway, consent_registry, "aether_add_expense",
                                    {"name": "lunch", "amount": 200}, channel=ConfirmationChannel.VOICE)
    await fully_authorize_and_call(phase4_gateway, rcap_gateway, consent_registry, "sensitive_demo_unlock_door",
                                    {"door": "front"}, channel=ConfirmationChannel.VOICE)  # denied
    await fully_authorize_and_call(phase4_gateway, rcap_gateway, consent_registry, "sensitive_demo_unlock_door",
                                    {"door": "front"}, channel=ConfirmationChannel.APP_TAP)  # allowed
    ok, bad_id = ledger.verify_chain()
    assert ok is True
    assert bad_id is None


def test_registry_correctly_tiers_every_tool(registry):
    assert registry["aether_add_expense"].sensitivity == ToolSensitivity.WRITE
    assert registry["aether_log_habit"].sensitivity == ToolSensitivity.WRITE
    assert registry["sensitive_demo_unlock_door"].sensitivity == ToolSensitivity.SENSITIVE
    assert registry["cinemavault_list_movies"].sensitivity == ToolSensitivity.READ_ONLY
