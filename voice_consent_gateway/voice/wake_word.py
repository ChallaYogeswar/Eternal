"""
Wake word detection — Eternal_Master_Build_Plan.md Section 3 ("Voice"):
"openWakeWord, wake word 'Hey Eternal'." Section 6.3, point 2: "This
suspicion extends to the wake-word/STT pipeline itself, not just the final
confirmation — a clone can trigger the wake word too" — i.e. the wake word
is a *trigger*, never treated as an identity or authorization signal on
its own, here or anywhere downstream.

Why openWakeWord itself isn't running in this sandbox: its real trigger is
a trained ONNX classifier. Confirmed during Phase 4 development that this
sandbox has neither a reachable host for its model files (same
network-allowlist situation Phase 2 hit with ollama.com/huggingface.co)
nor, more fundamentally, the disk budget for the onnxruntime dependency
stack at all (an unrelated openai-whisper install attempt failed with
`OSError: [Errno 28] No space left on device` on this same sandbox — see
stt.py — and onnxruntime is a comparable weight class).

WakeWordDetector below is a real, working abstraction with a real (if
deliberately crude) implementation: an RMS-energy-over-threshold detector
operating on genuine 16-bit PCM audio frames (voice/fixtures.py generates
real WAV bytes for its tests — synthesized speech via espeak-ng, and a
synthesized silence/tone pair for the negative/positive energy cases).
This is legitimate, decades-old voice-activity-detection technique, not a
mocked boolean — it just detects "something loud enough is happening,"
not "the specific phrase 'Hey Eternal' was spoken." The seam for the real
thing is exactly this class's interface: swap in an openwakeword.Model
call inside RealOpenWakeWordDetector.detect() once the target deployment
(mini-PC/Pi, not this sandbox) can install and load it.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List

from ..config import Config
from .fixtures import read_wav_pcm16


@dataclass
class WakeWordResult:
    triggered: bool
    detail: str
    frames_over_threshold: int = 0


class WakeWordDetector(ABC):
    @abstractmethod
    def detect(self, audio_bytes: bytes) -> WakeWordResult:
        ...


class RealOpenWakeWordDetector(WakeWordDetector):
    """Documented seam for the real thing. Raises immediately rather than
    silently degrading — a caller that reaches for this class on a machine
    without the real dependency stack installed should get a clear error,
    not a detector that quietly never triggers."""

    def __init__(self, model_path: str = "hey_eternal.onnx"):
        try:
            import openwakeword  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "openwakeword is not installed / its model assets are not "
                "reachable in this environment. Use RmsEnergyWakeWordDetector "
                "for sandboxed development and testing; install openwakeword "
                "and its 'hey_eternal' custom model on the real deployment "
                "target (mini-PC/Pi) to use this class for real."
            ) from e
        self.model_path = model_path

    def detect(self, audio_bytes: bytes) -> WakeWordResult:  # pragma: no cover - real hardware path
        raise NotImplementedError(
            "wire this to openwakeword.Model(...).predict(...) once model "
            "assets are actually available on the deployment target"
        )


class RmsEnergyWakeWordDetector(WakeWordDetector):
    """Real, working, deliberately-crude fallback: splits the given PCM16
    mono WAV into fixed-size frames, computes each frame's RMS, and
    triggers once at least `min_voiced_frames` consecutive-or-total frames
    exceed `rms_threshold`. This is a real signal-processing technique
    (classic voice-activity detection) — it genuinely distinguishes
    silence from speech-shaped energy in the fixture WAVs (see
    tests/test_wake_word.py) — it just isn't phrase-specific. In
    production this gates whether the (comparatively expensive) STT call
    even happens; here it's exercised directly so its own logic is
    provably tested independent of whatever comes after it.
    """

    def __init__(self, config: Config, frame_size: int = 512):
        self.config = config
        self.frame_size = frame_size

    def _frame_rms(self, frame: List[int]) -> float:
        if not frame:
            return 0.0
        return (sum(s * s for s in frame) / len(frame)) ** 0.5

    def detect(self, audio_bytes: bytes) -> WakeWordResult:
        try:
            samples, _rate = read_wav_pcm16(audio_bytes)
        except Exception as e:
            return WakeWordResult(triggered=False, detail=f"could not decode audio: {e}")

        if not samples:
            return WakeWordResult(triggered=False, detail="empty audio")

        frames_over = 0
        for start in range(0, len(samples), self.frame_size):
            frame = samples[start:start + self.frame_size]
            if self._frame_rms(frame) >= self.config.wake_word_rms_threshold:
                frames_over += 1

        triggered = frames_over >= self.config.wake_word_min_voiced_frames
        detail = (
            f"{frames_over} frame(s) over RMS threshold "
            f"{self.config.wake_word_rms_threshold:.0f} "
            f"(need >= {self.config.wake_word_min_voiced_frames})"
        )
        return WakeWordResult(triggered=triggered, detail=detail, frames_over_threshold=frames_over)
