"""Device attestation verification (audit finding E-02).

`Device.hardware_backed` used to be set to True for EVERY enrolled device, including ones holding a
`SimulatedHardwareKey` or a public key the client simply handed us. That made the hardware gate (FASTLANE and the
top trust tier) pass for any enrolled device. The flag is now decided ONLY by an `AttestationVerifier`, which looks at
evidence the platform produced (Android Key Attestation chain, Apple App Attest, TPM quote, WebAuthn attestation
statement, ...) and returns whether it validates.

No real platform verifier ships in this repo yet, so the default is `FailClosedVerifier`: nothing is ever marked
hardware-backed until you plug one in. Gates that need hardware backing therefore refuse until real attestation exists,
which is the honest state of the system.

`StaticVerifierForTests` exists so the many tests that exercise the hardware-gated paths can say so explicitly. It is
deliberately named, and `Orchestrator` logs `attestation_verifier` in every enrollment ledger entry, so a deployment
accidentally using it is visible in the audit trail.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class AttestationResult:
    verified: bool
    detail: str


class AttestationVerifier(ABC):
    name: str = "unnamed"

    @abstractmethod
    def verify(self, *, device_id: str, public_key_pem: str, attestation_type: str,
               evidence: Optional[Any]) -> AttestationResult:
        ...


class FailClosedVerifier(AttestationVerifier):
    """Default. Never verifies anything: simulated, client-provided and unattested keys are all 'not hardware-backed'."""
    name = "fail-closed (no platform attestation verifier installed)"

    def verify(self, *, device_id, public_key_pem, attestation_type, evidence) -> AttestationResult:
        return AttestationResult(False, "no attestation verifier installed; device is NOT verified hardware-backed")


class StaticVerifierForTests(AttestationVerifier):
    """TEST ONLY. Declares every device verified. Never use outside tests and local demos."""
    name = "static-verifier-FOR-TESTS-ONLY"

    def __init__(self, verified: bool = True):
        self._verified = verified

    def verify(self, *, device_id, public_key_pem, attestation_type, evidence) -> AttestationResult:
        return AttestationResult(self._verified, "static test verdict")
