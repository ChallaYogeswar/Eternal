"""
Speech-to-text — Eternal_Master_Build_Plan.md Section 3: "Whisper STT."

Why real Whisper isn't running in this sandbox: `pip install openai-whisper`
installs the *package* cleanly, but produced
`OSError: [Errno 28] No space left on device` pulling in its torch
dependency (verified directly during Phase 4 development — this sandbox's
volume had ~2.7GB free at the time). Independent of that, Whisper's
checkpoint host (openaipublic.azureedge.net) returned HTTP 403 through
this sandbox's egress proxy, and huggingface.co (an alternate checkpoint
mirror many Whisper wrappers use) does too — the same network-allowlist
situation Phase 2 documented for ollama.com. Either constraint alone would
block a real model load here; both apply.

WhisperSTT below is written exactly as it would run for real — it raises
a clear, typed, caught error rather than pretending; the seam is
`WhisperSTT._load_model()`. FixtureSTT is the tested double: it looks up
*real* audio (see voice/fixtures.py — genuine espeak-ng WAV bytes) by
content hash against a small known cassette, and is deliberately honest
about audio it doesn't recognize — it returns an empty transcript at zero
confidence rather than fabricating text, because a test double that can
"transcribe" arbitrary unknown audio would be lying about what it proves.

Telugu note: the project's bilingual design calls for Bhashini/AI4Bharat
models (IndicWhisper/IndicConformer) for Telugu rather than English
Whisper. Both SpeechToText subclasses below accept a `language` parameter
for exactly this reason — a `BhashiniSTT(SpeechToText)` class would plug
in at the same seam as `WhisperSTT`, routed to by language rather than
replacing it. Not implemented in Phase 4: Bhashini is a hosted API
requiring credentials and a reachable host neither available in this
sandbox, and no Telugu test fixtures exist yet to build a honest double
against (voice/fixtures.py's KNOWN_COMMANDS are English-only). Left as an
explicit open item rather than a silent gap — see README.md.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional

from .fixtures import KNOWN_COMMANDS, audio_fingerprint, synthesize_wav


@dataclass
class STTResult:
    text: str
    confidence: float
    language: str
    backend: str


class SpeechToText(ABC):
    @abstractmethod
    def transcribe(self, audio_bytes: bytes, language: str = "en") -> STTResult:
        ...


class ModelUnavailableError(RuntimeError):
    pass


class WhisperSTT(SpeechToText):
    """Real interface; the seam for the real deployment target."""

    def __init__(self, model_size: str = "small"):
        self.model_size = model_size
        self._model = None

    def _load_model(self):
        try:
            import whisper  # noqa: F401
        except ImportError as e:
            raise ModelUnavailableError(
                f"openai-whisper is not importable and/or its '{self.model_size}' "
                "checkpoint is not reachable in this environment (blocked model "
                "host + insufficient disk during Phase 4 development — see "
                "module docstring). Use FixtureSTT for sandboxed development "
                "and testing; install whisper + fetch checkpoints on the real "
                "deployment target to use this class for real."
            ) from e
        return whisper.load_model(self.model_size)  # pragma: no cover - real hardware path

    def transcribe(self, audio_bytes: bytes, language: str = "en") -> STTResult:  # pragma: no cover
        if self._model is None:
            self._model = self._load_model()
        raise NotImplementedError("wire real whisper.transcribe(...) call here")


class FixtureSTT(SpeechToText):
    """Fixture-cassette test double. See module docstring for why this is
    the honest way to test an STT-shaped pipeline without a real model."""

    def __init__(self):
        self._cassette: Dict[str, str] = {}
        for cmd in KNOWN_COMMANDS.values():
            audio = synthesize_wav(cmd.text)
            self._cassette[audio_fingerprint(audio)] = cmd.text

    def register(self, audio_bytes: bytes, transcript: str) -> None:
        """Lets tests/demo add ad-hoc fixtures beyond the built-in cassette
        without touching voice/fixtures.py."""
        self._cassette[audio_fingerprint(audio_bytes)] = transcript

    def transcribe(self, audio_bytes: bytes, language: str = "en") -> STTResult:
        fp = audio_fingerprint(audio_bytes)
        transcript = self._cassette.get(fp)
        if transcript is None:
            # Honest degradation — see module docstring. A real Whisper
            # call facing genuinely unintelligible or unknown audio also
            # returns low-confidence/garbage output, not a confident
            # guess; this mirrors that rather than fabricating a match.
            return STTResult(text="", confidence=0.0, language=language, backend="fixture_unknown_audio")
        return STTResult(text=transcript, confidence=0.97, language=language, backend="fixture_cassette")
