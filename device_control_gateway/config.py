"""
Phase 5 Configuration
----------------------
Same pattern as qap_p/config.py (Phase 1), rcap_p/config.py (Phase 3), and
voice_consent_gateway/config.py (Phase 4): every tunable documented against the specific
Eternal_Master_Build_Plan.md line it implements.
"""
import os
from dataclasses import dataclass


@dataclass
class Config:
    # --- Data / audit ---
    data_dir: str = os.environ.get("PHASE5_DATA_DIR", "./data")
    ledger_db_path: str = ""
    laptop_actions_db_path: str = ""
    home_assistant_db_path: str = ""

    # --- Top trust tier (build plan Section 7.1: "'take over full control
    # of my laptop' is the single most sensitive action in the whole
    # system — the top trust tier...") ---
    # Deliberately stricter than RCAP-P's own FASTLANE threshold
    # (rcap_p.config.Config.fastlane_min_trust_score, default 0.6) — see
    # trust_tier.py's module docstring for why these must be two numbers,
    # not one. 0.85 is a conservative starting point: high enough that a
    # freshly-enrolled or recently-quarantined device cannot reach it
    # (see qap_p/trust.py's scoring weights), documented rather than
    # hand-tuned against real usage data that doesn't exist yet — same
    # posture Phase 1 took with its own weights.
    top_trust_tier_min_score: float = float(os.environ.get("PHASE5_TOP_TRUST_MIN_SCORE", 0.85))

    # --- Laptop agent (build plan Section 7.1) ---
    # "Never a generic 'run this command' endpoint" — this is the fixed
    # allow-list of app names / script names the agent will accept. Real
    # deployments extend this list; it is never taken from caller input.
    laptop_allowed_apps: tuple = ("browser", "code_editor", "terminal", "mail")
    laptop_allowed_scripts: tuple = ("backup_notes", "sync_aether", "restart_ollama")
    wake_on_lan_broadcast_port: int = int(os.environ.get("PHASE5_WOL_PORT", 9))

    # --- Home Assistant (build plan Section 7.1: "Home devices: Home
    # Assistant joins the same Tailscale mesh; MCP-scoped tool access,
    # reachable from anywhere.") ---
    home_assistant_base_url: str = os.environ.get("HOME_ASSISTANT_BASE_URL", "")
    home_assistant_token: str = os.environ.get("HOME_ASSISTANT_TOKEN", "")
    home_assistant_timeout_seconds: float = float(os.environ.get("PHASE5_HA_TIMEOUT", 5.0))

    # --- Kill switch (build plan Section 7.1: "a kill switch that
    # immediately quarantines every session and revokes every scoped
    # token") ---
    kill_switch_reason_default: str = "PHASE5_KILL_SWITCH_MANUAL_TRIGGER"

    def __post_init__(self):
        os.makedirs(self.data_dir, exist_ok=True)
        self.ledger_db_path = self.ledger_db_path or os.path.join(self.data_dir, "phase5_ledger.db")
        self.laptop_actions_db_path = self.laptop_actions_db_path or os.path.join(self.data_dir, "laptop_actions.db")
        self.home_assistant_db_path = self.home_assistant_db_path or os.path.join(self.data_dir, "home_assistant_sim.db")
