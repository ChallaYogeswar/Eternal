"""
QAP-P Configuration
--------------------
Central place for the tunables mentioned throughout QAP_Whitepaper_CHALLAYOGESWAR.docx
and Eternal_Master_Build_Plan.md, scaled to personal use. Every default here is
documented against the source spec so a future you (or Phase 2+) can find the
reasoning without re-deriving it.
"""
import os
from dataclasses import dataclass


@dataclass
class Config:
    # QAP whitepaper Section 3.2: "QUIESCE state carries a mandatory
    # Time-to-Live (TTL) of 30 seconds, configurable per deployment."
    quiesce_ttl_seconds: float = float(os.environ.get("QAP_QUIESCE_TTL", 30))

    # QAP whitepaper Section 3.3: Authorizations are short-lived JWTs with
    # "a 30-second expiry timestamp."
    authorization_ttl_seconds: float = float(os.environ.get("QAP_AUTH_TTL", 30))

    # Build plan Section 6.1 "fix from design review", soft tier:
    # "same enrolled device, new IP/geo, otherwise-normal behavior
    # -> biometric + a cooldown window."
    soft_recovery_cooldown_seconds: float = float(os.environ.get("QAP_SOFT_COOLDOWN", 300))

    # QAP whitepaper Section 3.3: nodes below this trust-score threshold
    # (including recently onboarded nodes) are ineligible to issue
    # Authorizations to peers.
    authorization_issuance_threshold: float = float(os.environ.get("QAP_ISSUE_THRESHOLD", 0.55))

    # Build plan Section 4: "a low-trust session can't authorize full laptop
    # control or trade execution even if otherwise authenticated" — the gate
    # later phases (4/5/6) will use for sensitive-tier actions. Computed here
    # now so it's ready when they need it.
    sensitive_tier_threshold: float = float(os.environ.get("QAP_SENSITIVE_THRESHOLD", 0.7))

    # Default TTL for vault-issued capability tokens (short-lived, scoped —
    # never one master key; build plan Section 3 "Security infrastructure").
    default_token_ttl_seconds: float = float(os.environ.get("QAP_TOKEN_TTL", 300))

    # Audit E-01: upper bound for a vault token lifetime requested over the API.
    max_token_ttl_seconds: float = float(os.environ.get("QAP_MAX_TOKEN_TTL", 3600))

    # Audit E-02: when True, enrollment is refused unless a platform attestation verifier validated the device.
    # Default False because no real verifier ships yet; hardware_backed is still False for unverified devices either way.
    require_verified_attestation: bool = os.environ.get("QAP_REQUIRE_ATTESTATION", "0") == "1"

    # Local-first storage only (build plan Section 5.1) — no cloud dependency
    # for the trust substrate itself.
    data_dir: str = os.environ.get("QAP_DATA_DIR", "./data")
    db_path: str = ""
    vault_key_path: str = ""
    offline_recovery_key_path: str = ""

    def __post_init__(self):
        os.makedirs(self.data_dir, exist_ok=True)
        self.db_path = self.db_path or os.path.join(self.data_dir, "qap_p.db")
        self.vault_key_path = self.vault_key_path or os.path.join(self.data_dir, "vault.key")
        self.offline_recovery_key_path = self.offline_recovery_key_path or os.path.join(
            self.data_dir, "offline_recovery_key.txt"
        )
