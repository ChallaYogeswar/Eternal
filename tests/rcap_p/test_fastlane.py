from rcap_p.fastlane import FastlaneGate, TrustLookup
from rcap_p.models import CallerKind, ToolCallContext


class FakeTrustLookup:
    def __init__(self, state="ACTIVE", device_id="device-1", hardware_backed=True, score=0.9):
        self.state = state
        self.device_id = device_id
        self.hardware_backed = hardware_backed
        self.score = score

    def get_session_state(self, session_id):
        return self.state

    def compute_trust_score(self, device_id):
        return self.score

    def get_device_id_for_session(self, session_id):
        return self.device_id

    def is_hardware_backed(self, device_id):
        return self.hardware_backed


def test_automated_routine_never_gets_fastlane_even_with_perfect_trust(config):
    gate = FastlaneGate(config, trust_lookup=FakeTrustLookup(state="ACTIVE", score=1.0))
    ctx = ToolCallContext(session_id="sess-1", caller_kind=CallerKind.AUTOMATED_ROUTINE, source_label="scheduled_job")
    decision = gate.check(ctx)
    assert not decision.granted
    assert "no exceptions" in decision.reason


def test_owner_interactive_active_hardware_backed_good_trust_gets_fastlane(config):
    gate = FastlaneGate(config, trust_lookup=FakeTrustLookup(state="ACTIVE", hardware_backed=True, score=0.9))
    ctx = ToolCallContext(session_id="sess-1", caller_kind=CallerKind.OWNER_INTERACTIVE, source_label="owner_direct")
    decision = gate.check(ctx)
    assert decision.granted


def test_owner_interactive_but_not_active_session_is_denied(config):
    gate = FastlaneGate(config, trust_lookup=FakeTrustLookup(state="QUARANTINE"))
    ctx = ToolCallContext(session_id="sess-1", caller_kind=CallerKind.OWNER_INTERACTIVE, source_label="owner_direct")
    decision = gate.check(ctx)
    assert not decision.granted
    assert "ACTIVE" in decision.reason


def test_owner_interactive_but_not_hardware_backed_is_denied(config):
    gate = FastlaneGate(config, trust_lookup=FakeTrustLookup(hardware_backed=False))
    ctx = ToolCallContext(session_id="sess-1", caller_kind=CallerKind.OWNER_INTERACTIVE, source_label="owner_direct")
    decision = gate.check(ctx)
    assert not decision.granted
    assert "hardware" in decision.reason


def test_owner_interactive_but_low_trust_is_denied(config):
    gate = FastlaneGate(config, trust_lookup=FakeTrustLookup(score=0.1))
    ctx = ToolCallContext(session_id="sess-1", caller_kind=CallerKind.OWNER_INTERACTIVE, source_label="owner_direct")
    decision = gate.check(ctx)
    assert not decision.granted
    assert "trust score" in decision.reason


def test_no_session_id_is_denied(config):
    gate = FastlaneGate(config, trust_lookup=FakeTrustLookup())
    ctx = ToolCallContext(session_id=None, caller_kind=CallerKind.OWNER_INTERACTIVE, source_label="owner_direct")
    decision = gate.check(ctx)
    assert not decision.granted


def test_standalone_no_trust_backend_never_grants_fastlane(config):
    """The safe-failure default: RCAP-P run standalone (no Phase 1 QAP-P
    installed alongside it) must never fabricate trust — see
    fastlane.py's NullTrustLookup."""
    gate = FastlaneGate(config)  # no trust_lookup supplied -> NullTrustLookup
    ctx = ToolCallContext(session_id="sess-1", caller_kind=CallerKind.OWNER_INTERACTIVE, source_label="owner_direct")
    decision = gate.check(ctx)
    assert not decision.granted
