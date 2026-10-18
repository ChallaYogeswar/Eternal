import pytest

from voice_consent_gateway.voice.fixtures import KNOWN_COMMANDS, synthesize_wav
from voice_consent_gateway.voice.speaker_verify import EcapaTdnnSpeakerVerifier, FixtureSpeakerVerifier, ModelUnavailableError


def test_enrolled_sample_matches():
    verifier = FixtureSpeakerVerifier()
    sample = synthesize_wav(KNOWN_COMMANDS["enrolled_owner_sample"].text)
    verifier.enroll(sample, "owner")
    result = verifier.verify(sample, "owner")
    assert result.is_match is True
    assert result.score > 0.7


def test_unenrolled_audio_never_fabricates_a_match():
    verifier = FixtureSpeakerVerifier()
    unenrolled = synthesize_wav("a completely different sentence")
    result = verifier.verify(unenrolled, "owner")
    assert result.is_match is False
    assert result.score < 0.5


def test_wrong_profile_id_does_not_match():
    verifier = FixtureSpeakerVerifier()
    sample = synthesize_wav(KNOWN_COMMANDS["enrolled_owner_sample"].text)
    verifier.enroll(sample, "owner")
    result = verifier.verify(sample, "someone_else")
    assert result.is_match is False


def test_ecapa_seam_raises_clear_typed_error():
    verifier = EcapaTdnnSpeakerVerifier()
    with pytest.raises(ModelUnavailableError):
        verifier._load_model()


def test_speaker_verify_result_has_no_authorization_semantics():
    """Documentation-as-test: SpeakerVerifyResult carries no field that
    could be mistaken for an authorization decision (no `authorized`,
    no `confirmation_channel`) — voice_gate.py's SENSITIVE-tier check
    does not and cannot consult this module's output. See
    speaker_verify.py's and voice_gate.py's module docstrings."""
    verifier = FixtureSpeakerVerifier()
    sample = synthesize_wav(KNOWN_COMMANDS["enrolled_owner_sample"].text)
    verifier.enroll(sample, "owner")
    result = verifier.verify(sample, "owner")
    field_names = set(result.__dataclass_fields__.keys())
    assert field_names == {"is_match", "score", "enrolled_profile_id", "backend"}
