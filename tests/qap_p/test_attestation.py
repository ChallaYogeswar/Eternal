import pytest

from qap_p.attestation import FailClosedVerifier, StaticVerifierForTests
from qap_p.config import Config
from qap_p.hardware_key import HardwareKeyProvider, SimulatedHardwareKey
from qap_p.models import DeviceRole
from qap_p.orchestrator import Orchestrator


def orch(tmp_path, **kw):
    return Orchestrator(Config(data_dir=str(tmp_path)), **kw)


def test_default_is_fail_closed_for_simulated_key(tmp_path):
    d = orch(tmp_path).enroll_device("d1", DeviceRole("laptop"))
    assert d.hardware_backed is False


def test_client_supplied_key_is_not_hardware_backed(tmp_path):
    class Client(HardwareKeyProvider):
        def sign(self, c): raise NotImplementedError
        def public_key_pem(self): return SimulatedHardwareKey().public_key_pem()
        @property
        def attestation_type(self): return "client-provided (server cannot verify hardware backing remotely)"
    assert orch(tmp_path).enroll_device("d2", DeviceRole("phone"), Client()).hardware_backed is False


def test_provider_cannot_claim_hardware_backing_for_itself(tmp_path):
    class Liar(SimulatedHardwareKey):
        hardware_backed = True
        attestation_type = "genuine-hardware-trust-me"
    assert orch(tmp_path).enroll_device("d3", DeviceRole("phone"), Liar()).hardware_backed is False


def test_only_the_verifier_decides(tmp_path):
    assert orch(tmp_path, attestation_verifier=StaticVerifierForTests(True)).enroll_device(
        "d4", DeviceRole("laptop")).hardware_backed is True


def test_persisted_flag_survives_reload(tmp_path):
    o = orch(tmp_path)
    o.enroll_device("d5", DeviceRole("laptop"))
    assert Orchestrator(Config(data_dir=str(tmp_path))).get_device("d5").hardware_backed is False


def test_trust_score_lower_without_hardware_factor(tmp_path):
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    a = orch(tmp_path / "a")
    b = orch(tmp_path / "b", attestation_verifier=StaticVerifierForTests(True))
    a.enroll_device("x", DeviceRole("laptop")); b.enroll_device("x", DeviceRole("laptop"))
    assert a.compute_trust_score("x") < b.compute_trust_score("x")


def test_strict_mode_refuses_unverified_enrollment_and_logs_it(tmp_path):
    c = Config(data_dir=str(tmp_path)); c.require_verified_attestation = True
    o = Orchestrator(c)
    with pytest.raises(ValueError, match="attestation not verified"):
        o.enroll_device("d6", DeviceRole("laptop"))
    assert o.get_device("d6") is None
    assert any(e["event_type"] == "device_enrollment_refused" for e in o.ledger_entries(20))


def test_enrollment_ledger_entry_records_verifier_and_flag(tmp_path):
    o = orch(tmp_path)
    o.enroll_device("d7", DeviceRole("laptop"))
    e = [x for x in o.ledger_entries(20) if x["event_type"] == "device_enrolled"][0]
    assert e["payload"]["hardware_backed"] is False and "fail-closed" in e["payload"]["attestation_verifier"]
