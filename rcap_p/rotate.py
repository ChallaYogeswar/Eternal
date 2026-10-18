"""
Ring 3 — ROTATE.

RCAP whitepaper Section 3.2 (ring rotation seeds) maps, at personal scale,
onto Eternal_Master_Build_Plan.md Section 6.2: "Scheduled credential
rotation + session-scoped, not permanent, tool permissions." The
network-topology-rotation half of the whitepaper (re-seeding which node
answers for a ring position) has no real referent inside a single-user
assistant process — there's no attacker-facing network topology to keep
stale. What *does* port directly, and is what Phase 3 actually needs, is
the other half of the same sentence: tool access must be a short-lived
grant, not a standing capability, so a compromised session or a leaked
token doesn't equal permanent access.

Design choice — a self-contained token issuer, not a forced dependency on
qap_p.vault.Vault:

qap_p's Vault (Phase 1) issues scoped, short-lived tokens keyed by
`device_id`. RCAP-P's tool-permission grants are naturally keyed by
*session* (a session requests a tool call; Phase 1 sessions already carry
a device_id if you need to trace back further). Rather than stretch
qap_p's device-oriented Vault/Store schema to fit a session-oriented
concept, RotateVault below is a small, self-contained implementation using
the exact same design pattern (Fernet-encrypted payload; revocation tracked
independently in SQLite so a token can be killed before its cryptographic
TTL expires) — same idea qap_p already proved, re-applied to a different
key. If a later consolidation pass wants one shared vault subsystem for
both protocols (the whitepaper's own suggestion), this module is the seam:
swap RotateVault for an adapter around qap_p.vault.Vault without anything
else in RCAP-P changing, since the gateway only ever calls
grant()/check()/revoke_session() on whatever's constructed here.
"""
import os
import json
import time
import uuid
import sqlite3
from dataclasses import dataclass
from typing import Optional

from cryptography.fernet import Fernet

from .config import Config


class RotateError(Exception):
    pass


class RotateVault:
    def __init__(self, key_path: str, db_path: str):
        self.db_path = db_path
        self._fernet = self._load_or_create_key(key_path)
        self._init_db()

    @staticmethod
    def _load_or_create_key(key_path: str) -> Fernet:
        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            os.makedirs(os.path.dirname(key_path) or ".", exist_ok=True)
            with open(key_path, "wb") as f:
                f.write(key)
            os.chmod(key_path, 0o600)
        return Fernet(key)

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rotate_tokens (
                token_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                issued_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def issue_token(self, session_id: str, scope: str, ttl_seconds: float) -> str:
        token_id = str(uuid.uuid4())
        issued_at = time.time()
        expires_at = issued_at + ttl_seconds
        payload = {
            "token_id": token_id, "session_id": session_id,
            "scope": scope, "issued_at": issued_at, "expires_at": expires_at,
        }
        token = self._fernet.encrypt(json.dumps(payload).encode()).decode()
        conn = self._connect()
        conn.execute(
            "INSERT INTO rotate_tokens (token_id, session_id, scope, issued_at, expires_at, revoked) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (token_id, session_id, scope, issued_at, expires_at),
        )
        conn.commit()
        conn.close()
        return token

    def verify_token(self, token: str) -> dict:
        try:
            payload = json.loads(self._fernet.decrypt(token.encode()).decode())
        except Exception as e:
            raise RotateError(f"malformed or tampered permission token: {e}")
        conn = self._connect()
        row = conn.execute(
            "SELECT revoked FROM rotate_tokens WHERE token_id = ?", (payload["token_id"],)
        ).fetchone()
        conn.close()
        if row is None or row[0]:
            raise RotateError("permission token revoked or unknown")
        if time.time() > payload["expires_at"]:
            raise RotateError("permission token expired — call grant() again")
        return payload

    def revoke_token(self, token_id: str) -> None:
        conn = self._connect()
        conn.execute("UPDATE rotate_tokens SET revoked = 1 WHERE token_id = ?", (token_id,))
        conn.commit()
        conn.close()

    def revoke_all_for_session(self, session_id: str) -> int:
        conn = self._connect()
        cur = conn.execute(
            "UPDATE rotate_tokens SET revoked = 1 WHERE session_id = ? AND revoked = 0", (session_id,)
        )
        conn.commit()
        count = cur.rowcount
        conn.close()
        return count


@dataclass
class RotateDecision:
    allowed: bool
    reason: str = ""
    token_id: Optional[str] = None


class RotateRing:
    def __init__(self, config: Config, ledger, vault: Optional[RotateVault] = None):
        self.config = config
        self.ledger = ledger
        self.vault = vault or RotateVault(
            key_path=os.path.join(config.data_dir, "rotate_vault.key"),
            db_path=os.path.join(config.data_dir, "rotate_vault.db"),
        )

    def grant(self, session_id: str, tool_name: str) -> str:
        """Mints a fresh, short-lived permission token scoped to exactly
        one tool. Called explicitly (by the gateway, on the caller's
        behalf) rather than implicitly on every call, so "the permission
        expired" is a real, loggable, re-grantable event — not silently
        auto-renewed, which would make the TTL decorative."""
        scope = f"tool:{tool_name}"
        token = self.vault.issue_token(session_id, scope, self.config.rotate_token_ttl_seconds)
        self.ledger.append("tool_permission_granted", {
            "session_id": session_id, "tool_name": tool_name,
            "ttl_seconds": self.config.rotate_token_ttl_seconds,
        })
        return token

    def check(self, session_id: str, tool_name: str, token: Optional[str]) -> RotateDecision:
        if token is None:
            return RotateDecision(allowed=False, reason="no permission token presented; call grant() first")
        try:
            payload = self.vault.verify_token(token)
        except RotateError as e:
            return RotateDecision(allowed=False, reason=str(e))

        expected_scope = f"tool:{tool_name}"
        if payload["scope"] != expected_scope:
            return RotateDecision(
                allowed=False,
                reason=f"token scoped to '{payload['scope']}', not '{expected_scope}' — "
                       f"a token for one tool cannot authorize a different one",
            )
        if payload["session_id"] != session_id:
            return RotateDecision(
                allowed=False, reason="token was granted to a different session"
            )
        return RotateDecision(allowed=True, token_id=payload["token_id"])

    def revoke_session(self, session_id: str) -> int:
        count = self.vault.revoke_all_for_session(session_id)
        self.ledger.append("tool_permissions_revoked_for_session", {
            "session_id": session_id, "count": count,
        })
        return count
