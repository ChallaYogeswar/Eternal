"""
Speaker verification (ECAPA-TDNN) — part of Eternal's bilingual voice
interaction design (English/Telugu STT/TTS via Bhashini/AI4Bharat, speaker
verification via ECAPA-TDNN). Real ECAPA-TDNN inference needs a
speechbrain/torch-class dependency stack — the same disk-budget and
model-host constraints documented in stt.py and wake_word.py apply here
too, so this sandbox gets the same treatment: a real interface, a
documented seam, and a fixture-backed double for everything downstream.

The one rule that matters more than the implementation: a speaker match
is a SIGNAL, never an authorization. Eternal_Master_Build_Plan.md Section
6.3, point 2 says voice is "a separate risk surface from device trust,
full stop," and that a session being hardware-attested and ACTIVE "says
nothing about whether the audio is trustworthy — recordings and voice
clones bypass device trust entirely." A confident speaker match doesn't
change that calculus even slightly: a sufficiently good clone or replay
of Yogeswar's own enrolled voice sample would score *just as well* here as
the real thing, because that is exactly what a voice clone is *for*. This
module's result feeds voice/pipeline.py's `speaker_match`/`speaker_score`
fields for logging and UX ("hi Yogeswar") only; voice_gate.py's
SENSITIVE-tier rule does not read this module's output at all, by design,
not by oversight — see voice_gate.py's module docstring.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict

from .fixtures import audio_fingerprint


@dataclass
class SpeakerVerifyResult:
    is_match: bool
    score: float
    enrolled_profile_id: str
    backend: str


class SpeakerVerifier(ABC):
    @abstractmethod
    def verify(self, audio_bytes: bytes, enrolled_profile_id: str) -> SpeakerVerifyResult:
        ...


class ModelUnavailableError(RuntimeError):
    pass


class EcapaTdnnSpeakerVerifier(SpeakerVerifier):
    """Documented seam for the real deployment target."""

    def _load_model(self):
        try:
            import speechbrain  # noqa: F401
        except ImportError as e:
            raise ModelUnavailableError(
                "speechbrain (ECAPA-TDNN) is not importable in this "
                "environment. Use FixtureSpeakerVerifier for sandboxed "
                "development and testing; install speechbrain and enroll "
                "real voice profiles on the deployment target to use this "
                "class for real."
            ) from e
        return None  # pragma: no cover - real hardware path

    def verify(self, audio_bytes: bytes, enrolled_profile_id: str) -> SpeakerVerifyResult:  # pragma: no cover
        self._load_model()
        raise NotImplementedError("wire real ECAPA-TDNN embedding + cosine-similarity comparison here")


class FixtureSpeakerVerifier(SpeakerVerifier):
    """Fixture-cassette test double, same honesty rule as FixtureSTT:
    audio it doesn't recognize as a specific enrolled sample scores as a
    clear non-match, never a fabricated partial match."""

    def __init__(self):
        self._enrolled: Dict[str, str] = {}  # audio fingerprint -> profile_id

    def enroll(self, audio_bytes: bytes, profile_id: str) -> None:
        self._enrolled[audio_fingerprint(audio_bytes)] = profile_id

    def verify(self, audio_bytes: bytes, enrolled_profile_id: str) -> SpeakerVerifyResult:
        fp = audio_fingerprint(audio_bytes)
        matched_profile = self._enrolled.get(fp)
        if matched_profile == enrolled_profile_id:
            return SpeakerVerifyResult(
                is_match=True, score=0.93, enrolled_profile_id=enrolled_profile_id,
                backend="fixture_cassette",
            )
        return SpeakerVerifyResult(
            is_match=False, score=0.04, enrolled_profile_id=enrolled_profile_id,
            backend="fixture_cassette",
        )
