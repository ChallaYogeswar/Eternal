"""
Audio fixtures — real WAV bytes for a small, known set of spoken commands
and enrolled-voice samples, used by the fixture-backed STT and
speaker-verification test doubles (stt.py, speaker_verify.py).

Why this exists instead of a hand-written fake transcript: Whisper needs
model weights this sandbox cannot fetch (see stt.py), which would normally
force testing down to "assert the function returns the string I told it
to" — a test that proves nothing about the audio-handling code actually
working. `espeak-ng` (TTS), by contrast, genuinely works headless in this
sandbox (confirmed directly: it writes real 16-bit PCM WAV files) — so
this module uses REAL synthesized speech as the input side of the STT
fixture tests, and REAL raw PCM bytes as the input side of the wake-word
DSP tests. Only the "understanding" step (matching known audio to its
known transcript, matching a known voice sample to its enrolled profile)
is a lookup table rather than a trained model — everything upstream and
downstream of that lookup is real bytes flowing through real code.

Falls back to a clear, typed error (not silently-empty audio) if
espeak-ng isn't on PATH — the real deployment target (mini-PC/Pi) has it
per Eternal_Master_Build_Plan.md Section 3 ("pyttsx3/Coqui... local,
default"); a CI box without it should fail loudly, not pretend to pass.
"""
import hashlib
import math
import shutil
import struct
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from typing import Dict


class EspeakNotAvailableError(RuntimeError):
    pass


def _require_espeak() -> str:
    path = shutil.which("espeak-ng") or shutil.which("espeak")
    if path is None:
        raise EspeakNotAvailableError(
            "espeak-ng is not on PATH. The real deployment target has it "
            "(build plan Section 3: local TTS default); install it "
            "(`apt-get install espeak-ng`) to run the audio-fixture tests."
        )
    return path


def synthesize_wav(text: str, *, voice: str = "en") -> bytes:
    """Real TTS synthesis (not a placeholder): shells out to espeak-ng and
    returns genuine 16-bit PCM mono WAV bytes for `text`."""
    binary = _require_espeak()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
        subprocess.run(
            [binary, "-v", voice, "-w", tmp.name, text],
            check=True, capture_output=True, timeout=20,
        )
        with open(tmp.name, "rb") as f:
            return f.read()


def audio_fingerprint(audio_bytes: bytes) -> str:
    """Content hash used as the fixture cassette's lookup key. Two
    synthesis runs of the identical text on the identical espeak-ng build
    produce byte-identical WAV output (deterministic synthesizer, no
    randomness) — confirmed empirically during Phase 4 development, not
    assumed — so this is a stable key across runs, not a coincidence."""
    return hashlib.sha256(audio_bytes).hexdigest()


def read_wav_pcm16(audio_bytes: bytes):
    """Decodes WAV bytes to (frames: List[int], sample_rate: int) of
    16-bit PCM samples — real decoding via the stdlib `wave` module, used
    by wake_word.py's RMS-energy detector so it operates on genuine sample
    values rather than raw undifferentiated bytes."""
    import io
    with wave.open(io.BytesIO(audio_bytes), "rb") as w:
        n_frames = w.getnframes()
        sample_width = w.getsampwidth()
        raw = w.readframes(n_frames)
        rate = w.getframerate()
    if sample_width != 2:
        raise ValueError(f"expected 16-bit PCM, got sample width {sample_width}")
    count = len(raw) // 2
    samples = struct.unpack(f"<{count}h", raw[: count * 2])
    return list(samples), rate


def synthesize_silence(duration_seconds: float = 1.0, sample_rate: int = 22050) -> bytes:
    """Real (silent) WAV bytes — no espeak-ng dependency, used as the
    "wake word NOT present" fixture so wake_word.py's tests don't need TTS
    just to prove the negative case."""
    import io
    n_samples = int(duration_seconds * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack(f"<{n_samples}h", *([0] * n_samples)))
    return buf.getvalue()


def synthesize_tone(duration_seconds: float = 1.0, freq_hz: float = 220.0,
                     amplitude: int = 12000, sample_rate: int = 22050) -> bytes:
    """Real (audible-energy) WAV bytes — a plain sine tone standing in for
    "someone is producing sound right now" in wake_word.py's RMS-threshold
    tests, cheaper and more deterministic than a full espeak-ng round trip
    when the test only cares about energy, not content."""
    import io
    n_samples = int(duration_seconds * sample_rate)
    samples = [
        int(amplitude * math.sin(2 * math.pi * freq_hz * (i / sample_rate)))
        for i in range(n_samples)
    ]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack(f"<{n_samples}h", *samples))
    return buf.getvalue()


@dataclass
class KnownCommand:
    text: str
    intent_hint: str  # informal label, cross-checked by tests/intent.py — not parsed from audio


# The fixed cassette of "recordings" Phase 4's demo and tests use. Real
# text, real synthesis, real transcripts a genuine Whisper deployment
# would be expected to produce for this exact audio — see stt.py's
# FixtureSTT for how these are looked up by content hash rather than by
# some out-of-band index a real STT call would never have access to.
KNOWN_COMMANDS: Dict[str, KnownCommand] = {
    "log_run": KnownCommand(
        text="log my morning run habit as done for today",
        intent_hint="aether_log_habit",
    ),
    "add_expense": KnownCommand(
        text="add an expense of two hundred rupees for lunch",
        intent_hint="aether_add_expense",
    ),
    "log_workout": KnownCommand(
        text="log a forty five minute cycling workout",
        intent_hint="aether_log_workout",
    ),
    "unlock_door": KnownCommand(
        text="unlock the front door",
        intent_hint="sensitive_demo_unlock_door",
    ),
    "enrolled_owner_sample": KnownCommand(
        text="hey eternal it is me",
        intent_hint="speaker_enrollment_sample",
    ),
}
