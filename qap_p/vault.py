"""
Vault: issues short-lived, scoped tokens per capability instead of one
master key.

Eternal_Master_Build_Plan.md Section 3 "Security infrastructure", and
Section 9's open decision: "Vault software: HashiCorp Vault vs.
sops/age-encrypted files (leaning toward the latter for solo-scale
simplicity)."

This implementation takes the sops/age-style approach: a single, locally
held symmetric key encrypts token payloads at rest. There is no master key
that, if leaked, unlocks every capability at once — each *token* is scoped
and short-lived, and revocation state lives in the Store independently of
the encrypted payload, so a token can be killed even before its TTL expires.

Swapping this for an actual `age`/`sops`-encrypted file or a HashiCorp Vault
client later is a change local to this module — nothing else in QAP-P
depends on the storage mechanism, only on issue_token/verify_token/revoke.
"""
import os
import json
import time
import uuid

from cryptography.fernet import Fernet

from .store import Store
from .ledger import Ledger


class VaultError(Exception):
    pass


class Vault:
    def __init__(self, key_path: str, store: Store, ledger: Ledger):
        self.store = store
        self.ledger = ledger
        self._fernet = self._load_or_create_key(key_path)

    @staticmethod
    def _load_or_create_key(key_path: str) -> Fernet:
        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            with open(key_path, "wb") as f:
                f.write(key)
            os.chmod(key_path, 0o600)
        return Fernet(key)

    def issue_token(self, device_id: str, scope: str, ttl_seconds: float) -> str:
        token_id = str(uuid.uuid4())
        issued_at = time.time()
        expires_at = issued_at + ttl_seconds
        payload = {
            "token_id": token_id,
            "device_id": device_id,
            "scope": scope,
            "issued_at": issued_at,
            "expires_at": expires_at,
        }
        token = self._fernet.encrypt(json.dumps(payload).encode()).decode()
        self.store.create_token_record(token_id, device_id, scope, issued_at, expires_at)
        self.ledger.append("token_issued", {
            "token_id": token_id, "device_id": device_id, "scope": scope, "ttl_seconds": ttl_seconds,
        })
        return token

    def verify_token(self, token: str) -> dict:
        try:
            payload = json.loads(self._fernet.decrypt(token.encode()).decode())
        except Exception as e:
            raise VaultError(f"malformed or tampered token: {e}")
        if self.store.is_token_revoked(payload["token_id"]):
            raise VaultError("token revoked or unknown")
        if time.time() > payload["expires_at"]:
            raise VaultError("token expired")
        return payload

    def revoke_token(self, token_id: str) -> None:
        self.store.revoke_token(token_id)
        self.ledger.append("token_revoked", {"token_id": token_id})

    def revoke_all_for_device(self, device_id: str) -> int:
        count = self.store.revoke_all_tokens_for_device(device_id)
        self.ledger.append("tokens_revoked_for_device", {"device_id": device_id, "count": count})
        return count
