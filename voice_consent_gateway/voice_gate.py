"""
Voice gate — the mechanism behind the Phase 4 exit criterion's second
clause: "no sensitive-tier action can complete on voice alone."

Eternal_Master_Build_Plan.md Section 6.3, point 2, verbatim: "Sensitive-
tier actions (trade execution, full laptop control, physical Home
Assistant actions like locks) are never voice-triggerable alone,
regardless of trust score. Require a second, non-voice confirmation — a
tap in the phone app, not a spoken 'yes.'"

Three things about how this is enforced are deliberate, not incidental:

1. This check runs in Phase4Gateway BEFORE consent and BEFORE RCAP-P's
   own gateway is even called — "regardless of trust score" means this
   cannot be a check that FASTLANE eligibility, a high QAP-P trust score,
   or an existing per-tool consent grant can route around. A SENSITIVE
   tool call arriving with confirmation_channel=VOICE is refused here,
   full stop, before any of those other mechanisms get a chance to say
   yes.

2. It does not consult voice/speaker_verify.py's result at all. A
   confident speaker match is *still voice* — see speaker_verify.py's
   module docstring on why a clone scores just as well as the real thing.
   Passing a `speaker_match=True` into this function would only invite a
   future caller to try to use it as a bypass; the function signature
   deliberately doesn't accept that parameter.

3. The refusal is a clean, typed result explaining what channel WOULD
   satisfy it (APP_TAP) — mirroring RCAP-P's whole "flag, don't silently
   strip/crash" posture from Phase 3, applied to a denial instead of a
   content flag.
"""
from dataclasses import dataclass

from rcap_p.models import ToolManifestEntry, ToolSensitivity

from .models import ConfirmationChannel


@dataclass
class VoiceGateDecision:
    allowed: bool
    reason: str


NON_VOICE_CHANNELS = frozenset({
    ConfirmationChannel.TEXT_TYPED,
    ConfirmationChannel.APP_TAP,
    ConfirmationChannel.AUTOMATED,
})


def check_voice_gate(manifest: ToolManifestEntry, confirmation_channel: ConfirmationChannel) -> VoiceGateDecision:
    if manifest.sensitivity is not ToolSensitivity.SENSITIVE:
        return VoiceGateDecision(allowed=True, reason="not a SENSITIVE-tier tool; voice gate does not apply")

    if confirmation_channel is ConfirmationChannel.VOICE:
        return VoiceGateDecision(
            allowed=False,
            reason=(
                f"'{manifest.tool_name}' is SENSITIVE-tier and this request arrived over VOICE. "
                "Per build plan Section 6.3 point 2, sensitive-tier actions are never "
                "voice-triggerable alone, regardless of trust score or speaker match. "
                "Confirm via a non-voice channel (a tap in the phone app) to proceed."
            ),
        )

    assert confirmation_channel in NON_VOICE_CHANNELS  # exhaustiveness guard, not a runtime policy
    return VoiceGateDecision(
        allowed=True,
        reason=f"SENSITIVE-tier tool confirmed via non-voice channel '{confirmation_channel.value}'",
    )
