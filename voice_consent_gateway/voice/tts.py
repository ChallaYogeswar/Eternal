"""
Text-to-speech — Eternal_Master_Build_Plan.md Section 3: "pyttsx3/Coqui
(local, default) — ElevenLabs (cloud) only for non-sensitive output."

Unlike STT/wake-word/speaker-verification, LOCAL TTS genuinely runs for
real in this sandbox: `pyttsx3` with the `espeak-ng` driver was confirmed
during Phase 4 development to produce real 16-bit PCM WAV output headless,
no display or audio device required. `LocalTTS` below is therefore a real
implementation, not a stub — the honest-seam treatment in this package is
reserved for the pieces that genuinely can't run here (Whisper, ECAPA-TDNN,
openWakeWord's ONNX model, and — here — the *cloud* TTS backend, which has
no reachable host/API key in this sandbox and is stubbed exactly the way
rcap_p's web_search backend was in Phase 3).

The important piece isn't either backend alone; it's `PolicyEnforcingTTS`,
which is the actual enforcement point for Section 6.3, point 3's outbound
rule. It ignores whatever backend a caller *asks* for when the content
classifies as LOCAL_ONLY — the policy is not opt-in per call, exactly
because a careless or compromised caller asking for the cloud voice on
sensitive content is precisely the failure mode the build plan is
guarding against.
"""
import io
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..models import MemoryCategory
from .data_classification import DataClass, classify


@dataclass
class TTSResult:
    audio_bytes: bytes
    backend_used: str
    data_class: DataClass
    reasons: list


class TextToSpeech(ABC):
    @abstractmethod
    def speak(self, text: str) -> bytes:
        ...


class LocalTTS(TextToSpeech):
    """Real implementation: pyttsx3 -> espeak-ng, confirmed working
    headless in this sandbox. This is exactly the "Coqui/pyttsx3, Ollama"
    local half of Section 6.3 point 3's policy, and the only backend ever
    used for LOCAL_ONLY-classified content (see PolicyEnforcingTTS)."""

    def __init__(self, voice: str = "en"):
        self.voice = voice

    def speak(self, text: str) -> bytes:
        import pyttsx3
        import tempfile

        engine = pyttsx3.init(driverName="espeak")
        try:
            for v in engine.getProperty("voices") or []:
                if self.voice in (v.id or "") or self.voice in (v.languages[0] if v.languages else ""):
                    engine.setProperty("voice", v.id)
                    break
        except Exception:
            pass  # fall back to the default voice rather than fail the whole call over voice selection

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            engine.save_to_file(text, tmp.name)
            engine.runAndWait()
            with open(tmp.name, "rb") as f:
                return f.read()


class CloudTTSUnavailableError(RuntimeError):
    pass


class ElevenLabsCloudTTS(TextToSpeech):
    """Honest stub — same posture as rcap_p.mcp_servers.web_search.backend
    in Phase 3: no reachable host, no API key configured in this sandbox.
    Raises rather than fabricating audio, and — critically —
    PolicyEnforcingTTS never even calls this for LOCAL_ONLY content, so
    its unavailability here can't accidentally look like "the policy
    degraded gracefully to local" when actually cloud was simply broken."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    def speak(self, text: str) -> bytes:
        raise CloudTTSUnavailableError(
            "ElevenLabs is not reachable / no API key configured in this "
            "sandbox. On the real deployment this backend is only ever "
            "invoked for CLOUD_OK-classified, non-sensitive output — see "
            "PolicyEnforcingTTS."
        )


def _silent_frames_seconds(seconds: float = 0.1, rate: int = 22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class PolicyEnforcingTTS:
    """The actual enforcement point for build plan Section 6.3 point 3.
    `requested_backend` is a hint, not a guarantee — LOCAL_ONLY content is
    always routed to `local`, full stop, regardless of what was asked
    for."""

    def __init__(self, local: LocalTTS, cloud: TextToSpeech):
        self.local = local
        self.cloud = cloud

    def speak(self, text: str, *, domain_hint: Optional[MemoryCategory] = None,
              requested_backend: str = "cloud") -> TTSResult:
        classification = classify(text, domain_hint=domain_hint)

        if classification.data_class is DataClass.LOCAL_ONLY:
            audio = self.local.speak(text)
            return TTSResult(audio, "local", classification.data_class, classification.reasons)

        if requested_backend == "local":
            audio = self.local.speak(text)
            return TTSResult(audio, "local", classification.data_class, classification.reasons)

        try:
            audio = self.cloud.speak(text)
            return TTSResult(audio, "cloud", classification.data_class, classification.reasons)
        except CloudTTSUnavailableError:
            # Cloud is allowed by policy but unreachable in this sandbox —
            # fail over to local rather than produce no audio at all. This
            # is a *widening* fallback only ever taken for content the
            # policy already cleared for cloud; it never runs for
            # LOCAL_ONLY content, which returns above before reaching here.
            audio = self.local.speak(text)
            return TTSResult(audio, "local_fallback_cloud_unavailable", classification.data_class, classification.reasons)
