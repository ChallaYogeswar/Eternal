import pytest

from voice_consent_gateway.voice.fixtures import synthesize_silence, synthesize_tone, synthesize_wav
from voice_consent_gateway.voice.wake_word import RealOpenWakeWordDetector, RmsEnergyWakeWordDetector


def test_silence_does_not_trigger(config):
    det = RmsEnergyWakeWordDetector(config)
    result = det.detect(synthesize_silence(1.0))
    assert result.triggered is False
    assert result.frames_over_threshold == 0


def test_tone_triggers(config):
    det = RmsEnergyWakeWordDetector(config)
    result = det.detect(synthesize_tone(1.0, amplitude=12000))
    assert result.triggered is True
    assert result.frames_over_threshold >= config.wake_word_min_voiced_frames


def test_real_synthesized_speech_triggers(config):
    det = RmsEnergyWakeWordDetector(config)
    audio = synthesize_wav("hey eternal it is me")
    result = det.detect(audio)
    assert result.triggered is True


def test_quiet_tone_below_threshold_does_not_trigger(config):
    det = RmsEnergyWakeWordDetector(config)
    result = det.detect(synthesize_tone(1.0, amplitude=50))  # well under default 500 RMS threshold
    assert result.triggered is False


def test_threshold_is_configurable(config):
    config.wake_word_rms_threshold = 100000.0  # absurdly high — nothing should trigger
    det = RmsEnergyWakeWordDetector(config)
    result = det.detect(synthesize_tone(1.0, amplitude=12000))
    assert result.triggered is False


def test_malformed_audio_fails_closed_not_crash(config):
    det = RmsEnergyWakeWordDetector(config)
    result = det.detect(b"this is not a wav file")
    assert result.triggered is False
    assert "could not decode" in result.detail


def test_real_backend_raises_clear_seam_error():
    with pytest.raises(RuntimeError, match="openwakeword"):
        RealOpenWakeWordDetector()
