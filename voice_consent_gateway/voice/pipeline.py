"""
Voice pipeline — composes wake_word.py + stt.py + speaker_verify.py into
one "voice turn," and is the ONLY place in Phase 4 that is allowed to
produce a VoiceTurnContext with confirmation_channel=VOICE. Everything
downstream (gateway.py, voice_gate.py) trusts that tag; it is this
module's job to make sure it is only ever set when audio genuinely came
in through this pipeline, not just asserted by a careless caller.

Order of operations mirrors the real risk model, not just convenience:
wake word gates whether STT even runs (so an idle mic doesn't waste STT
cycles / send audio anywhere in a real cloud-STT deployment); STT gates
whether there is a transcript worth acting on at all; speaker verification
runs last and is explicitly informational (see speaker_verify.py's module
docstring) — a low or missing speaker match does not block the turn or
change confirmation_channel, it only annotates the result for logging and
personalization ("hi Yogeswar" vs a generic reply).
"""
from dataclasses import dataclass
from typing import Optional

from ..models import ConfirmationChannel, VoiceTurnContext
from .speaker_verify import SpeakerVerifier
from .stt import STTResult, SpeechToText
from .wake_word import WakeWordDetector, WakeWordResult


@dataclass
class VoiceTurnOutcome:
    context: Optional[VoiceTurnContext]
    wake_word: WakeWordResult
    stt: Optional[STTResult]
    rejected_reason: Optional[str]


class VoicePipeline:
    def __init__(
        self,
        wake_word_detector: WakeWordDetector,
        stt: SpeechToText,
        speaker_verifier: Optional[SpeakerVerifier] = None,
        enrolled_profile_id: str = "owner",
    ):
        self.wake_word_detector = wake_word_detector
        self.stt = stt
        self.speaker_verifier = speaker_verifier
        self.enrolled_profile_id = enrolled_profile_id

    def process_turn(self, audio_bytes: bytes, session_id: Optional[str] = None,
                      language: str = "en") -> VoiceTurnOutcome:
        wake_result = self.wake_word_detector.detect(audio_bytes)
        if not wake_result.triggered:
            return VoiceTurnOutcome(
                context=None, wake_word=wake_result, stt=None,
                rejected_reason=f"wake word not detected ({wake_result.detail})",
            )

        stt_result = self.stt.transcribe(audio_bytes, language=language)
        if not stt_result.text:
            return VoiceTurnOutcome(
                context=None, wake_word=wake_result, stt=stt_result,
                rejected_reason="wake word triggered but no intelligible transcript",
            )

        speaker_match: Optional[bool] = None
        speaker_score: Optional[float] = None
        if self.speaker_verifier is not None:
            sv = self.speaker_verifier.verify(audio_bytes, self.enrolled_profile_id)
            speaker_match, speaker_score = sv.is_match, sv.score

        context = VoiceTurnContext(
            session_id=session_id,
            raw_transcript=stt_result.text,
            transcript_confidence=stt_result.confidence,
            wake_word_triggered=True,
            speaker_match=speaker_match,
            speaker_score=speaker_score,
            language=language,
        )
        return VoiceTurnOutcome(context=context, wake_word=wake_result, stt=stt_result, rejected_reason=None)
