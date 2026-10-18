"""
Peer-issued Authorization flow.

QAP whitepaper Section 3.3, mapped to personal scale in
Eternal_Master_Build_Plan.md Section 6.1:

    "Peer-issued Authorization JWT (short-lived, non-replayable nonce)"
    -> "Your phone, as trusted peer, signs a short-lived token to revive
        another device's session — passwordless reauth"

Flow:
  1. The orchestrator issues a single-use nonce (challenge) scoped to a
     target session.
  2. The issuing device (the phone) signs that nonce with its hardware-
     backed key and presents the signature.
  3. The orchestrator verifies the signature against the phone's enrolled
     public key, checks the phone's trust score against the issuance
     threshold (QAP 3.3), and only then mints a short-lived,
     orchestrator-signed JWT "Authorization" scoped to (issuing device,
     target session, nonce).
  4. The target session redeems that JWT once to transition back to ACTIVE.

Nonces are single-use (QAP 4.5 / 3.3, "non-replayable") — the registry below
marks a nonce used the moment an Authorization is minted for it, since
minting a *second* Authorization from the same challenge is the replay this
is meant to prevent, not just redeeming the resulting JWT twice.
"""
import time
import uuid
from typing import Optional

import jwt
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization

from .ledger import Ledger


class AuthorizationError(Exception):
    pass


def _load_public_key(pem: str):
    return serialization.load_pem_public_key(pem.encode())


class AuthorizationService:
    def __init__(self, orchestrator_secret: str, ledger: Ledger, ttl_seconds: float = 30):
        self._secret = orchestrator_secret
        self.ledger = ledger
        self.ttl_seconds = ttl_seconds
        self._nonces = {}  # nonce -> {"target_session_id", "issued_at", "used"}

    def issue_challenge(self, target_session_id: str) -> str:
        nonce = str(uuid.uuid4())
        self._nonces[nonce] = {
            "target_session_id": target_session_id,
            "issued_at": time.time(),
            "used": False,
        }
        self.ledger.append("authorization_challenge_issued", {
            "target_session_id": target_session_id, "nonce": nonce,
        })
        return nonce

    def issue_authorization(self, issuing_device_id: str, issuing_public_key_pem: str,
                             target_session_id: str, nonce: str, signature: bytes) -> str:
        record = self._nonces.get(nonce)
        if record is None:
            raise AuthorizationError("unknown or expired nonce")
        if record["used"]:
            raise AuthorizationError("nonce already used — replay rejected")
        if record["target_session_id"] != target_session_id:
            raise AuthorizationError("nonce does not match target session")

        public_key = _load_public_key(issuing_public_key_pem)
        try:
            public_key.verify(signature, nonce.encode())
        except InvalidSignature:
            self.ledger.append("authorization_signature_rejected", {
                "issuing_device_id": issuing_device_id, "target_session_id": target_session_id,
            })
            raise AuthorizationError("signature verification failed")

        record["used"] = True  # burn the nonce now — this is the replay boundary

        now = time.time()
        token = jwt.encode(
            {
                "iss": "qap-p-orchestrator",
                "issuing_device": issuing_device_id,
                "target_session": target_session_id,
                "nonce": nonce,
                "iat": now,
                "exp": now + self.ttl_seconds,
            },
            self._secret,
            algorithm="HS256",
        )
        self.ledger.append("authorization_issued", {
            "issuing_device_id": issuing_device_id,
            "target_session_id": target_session_id,
            "nonce": nonce,
        })
        return token

    def redeem_authorization(self, token: str, expected_session_id: Optional[str] = None) -> dict:
        try:
            payload = jwt.decode(token, self._secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise AuthorizationError("authorization expired")
        except jwt.InvalidTokenError:
            raise AuthorizationError("invalid or forged authorization token")

        if expected_session_id and payload.get("target_session") != expected_session_id:
            raise AuthorizationError("authorization does not match this session")

        self.ledger.append("authorization_redeemed", {
            "target_session_id": payload["target_session"], "nonce": payload["nonce"],
        })
        return payload
