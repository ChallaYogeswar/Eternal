import pytest

from rcap_p.models import ToolManifestEntry, ToolSensitivity

from voice_consent_gateway.models import ConfirmationChannel
from voice_consent_gateway.voice_gate import check_voice_gate


def _manifest(sensitivity: ToolSensitivity) -> ToolManifestEntry:
    return ToolManifestEntry(
        tool_name="some_tool", server_key="some_server", description="d", sensitivity=sensitivity,
    )


@pytest.mark.parametrize("sensitivity", [ToolSensitivity.READ_ONLY, ToolSensitivity.WRITE])
def test_non_sensitive_tools_are_never_gated_by_channel(sensitivity):
    for channel in ConfirmationChannel:
        decision = check_voice_gate(_manifest(sensitivity), channel)
        assert decision.allowed is True


def test_sensitive_tool_denied_over_voice():
    decision = check_voice_gate(_manifest(ToolSensitivity.SENSITIVE), ConfirmationChannel.VOICE)
    assert decision.allowed is False
    assert "SENSITIVE-tier" in decision.reason
    assert "VOICE" in decision.reason


@pytest.mark.parametrize("channel", [
    ConfirmationChannel.TEXT_TYPED, ConfirmationChannel.APP_TAP, ConfirmationChannel.AUTOMATED,
])
def test_sensitive_tool_allowed_over_non_voice_channels(channel):
    decision = check_voice_gate(_manifest(ToolSensitivity.SENSITIVE), channel)
    assert decision.allowed is True


def test_voice_gate_function_signature_cannot_accept_a_speaker_match_bypass():
    """Documentation-as-test: check_voice_gate's signature has no
    parameter through which a caller could pass a speaker-verification
    result to influence the decision — see voice_gate.py's module
    docstring, point 2."""
    import inspect
    params = set(inspect.signature(check_voice_gate).parameters.keys())
    assert params == {"manifest", "confirmation_channel"}
