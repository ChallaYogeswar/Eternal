import wave
import io

import pytest

from voice_consent_gateway.models import MemoryCategory
from voice_consent_gateway.voice.data_classification import DataClass
from voice_consent_gateway.voice.tts import CloudTTSUnavailableError, ElevenLabsCloudTTS, LocalTTS, PolicyEnforcingTTS


def _is_real_wav(b: bytes) -> bool:
    try:
        with wave.open(io.BytesIO(b), "rb") as w:
            return w.getnframes() > 0
    except Exception:
        return False


def test_local_tts_produces_real_audio():
    audio = LocalTTS().speak("logging your morning run as done")
    assert _is_real_wav(audio)


def test_cloud_tts_stub_raises_clear_error():
    with pytest.raises(CloudTTSUnavailableError):
        ElevenLabsCloudTTS().speak("hello")


def test_policy_forces_local_for_finance_content_even_when_cloud_requested():
    tts = PolicyEnforcingTTS(LocalTTS(), ElevenLabsCloudTTS())
    result = tts.speak("you spent two hundred rupees on lunch", requested_backend="cloud")
    assert result.backend_used == "local"
    assert result.data_class == DataClass.LOCAL_ONLY
    assert _is_real_wav(result.audio_bytes)


def test_policy_forces_local_for_domain_hinted_health_content():
    tts = PolicyEnforcingTTS(LocalTTS(), ElevenLabsCloudTTS())
    result = tts.speak("all normal today", domain_hint=MemoryCategory.HEALTH, requested_backend="cloud")
    assert result.backend_used == "local"


def test_casual_content_falls_back_to_local_when_cloud_unreachable():
    """Cloud IS allowed by policy for casual content; it's simply
    unreachable in this sandbox (no API key/host) — the dispatcher must
    fail over to local rather than produce no audio."""
    tts = PolicyEnforcingTTS(LocalTTS(), ElevenLabsCloudTTS())
    result = tts.speak("the weather today is sunny", requested_backend="cloud")
    assert result.data_class == DataClass.CLOUD_OK
    assert result.backend_used == "local_fallback_cloud_unavailable"
    assert _is_real_wav(result.audio_bytes)


def test_explicit_local_request_for_casual_content_never_touches_cloud():
    class ExplodingCloud(ElevenLabsCloudTTS):
        def speak(self, text):
            raise AssertionError("cloud backend must not be called when local was explicitly requested")

    tts = PolicyEnforcingTTS(LocalTTS(), ExplodingCloud())
    result = tts.speak("what's the weather like", requested_backend="local")
    assert result.backend_used == "local"
