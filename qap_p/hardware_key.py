"""
Hardware-backed key abstraction.

QAP whitepaper Section 3.2 ("TPM-signed load certificate") mapped to
personal scale in Eternal_Master_Build_Plan.md Section 6.1: "Hardware key
already in your devices — Secure Enclave / Android StrongBox / Windows
Hello."

This is the seam where real device attestation plugs in later:
  - Android:      StrongBox-backed key in the Android Keystore
                  (non-exportable, attested via KeyAttestation)
  - iOS:          Secure Enclave key via CryptoKit's
                  SecureEnclave.P256.Signing.PrivateKey
  - Windows:      Windows Hello for Business / TPM-backed key, exposed to an
                  app through the WebAuthn platform authenticator
  - Pi / mini-PC: TPM 2.0 chip if present, else this simulated fallback

All of these expose the same two operations that matter to QAP-P: sign a
challenge, and never let the private key material leave the secure element.
`SimulatedHardwareKey` models that *contract* in software; it is NOT an
actual hardware root of trust. Swap it for a real platform provider before
the "hardware-backed" claim is genuinely load-bearing.
"""
from abc import ABC, abstractmethod

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization


class HardwareKeyProvider(ABC):
    @abstractmethod
    def sign(self, challenge: bytes) -> bytes:
        ...

    @abstractmethod
    def public_key_pem(self) -> str:
        ...

    @property
    @abstractmethod
    def attestation_type(self) -> str:
        ...


class SimulatedHardwareKey(HardwareKeyProvider):
    """Dev/test stand-in — a real Ed25519 keypair held in-process. Enough to
    exercise every signing/verification code path in QAP-P; not a hardware
    root of trust. See module docstring for the real integration points."""

    def __init__(self):
        self._private_key = ed25519.Ed25519PrivateKey.generate()

    def sign(self, challenge: bytes) -> bytes:
        return self._private_key.sign(challenge)

    def public_key_pem(self) -> str:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    @property
    def attestation_type(self) -> str:
        return "simulated-dev-key (NOT hardware-backed — see module docstring)"
