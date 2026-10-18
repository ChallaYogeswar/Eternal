import pytest

from voice_consent_gateway.voice.fixtures import KNOWN_COMMANDS, synthesize_silence, synthesize_wav
from voice_consent_gateway.voice.stt import FixtureSTT, ModelUnavailableError, WhisperSTT


def test_known_command_transcribes_correctly():
    stt = FixtureSTT()
    audio = synthesize_wav(KNOWN_COMMANDS["log_run"].text)
    result = stt.transcribe(audio)
    assert result.text == KNOWN_COMMANDS["log_run"].text
    assert result.confidence > 0.9
    assert result.backend == "fixture_cassette"


def test_unknown_audio_degrades_honestly_not_fabricated():
    stt = FixtureSTT()
    result = stt.transcribe(synthesize_silence(1.0))
    assert result.text == ""
    assert result.confidence == 0.0
    assert result.backend == "fixture_unknown_audio"


def test_ad_hoc_registration():
    stt = FixtureSTT()
    audio = synthesize_wav("this is a brand new test phrase not in the built-in cassette")
    assert stt.transcribe(audio).text == ""
    stt.register(audio, "this is a brand new test phrase not in the built-in cassette")
    assert stt.transcribe(audio).text == "this is a brand new test phrase not in the built-in cassette"


def test_all_known_commands_round_trip():
    stt = FixtureSTT()
    for cmd in KNOWN_COMMANDS.values():
        audio = synthesize_wav(cmd.text)
        assert stt.transcribe(audio).text == cmd.text


def test_whisper_seam_raises_clear_typed_error():
    whisper_stt = WhisperSTT()
    with pytest.raises(ModelUnavailableError):
        whisper_stt._load_model()
