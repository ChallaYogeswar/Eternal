"""
Phase 4 Configuration
----------------------
Same pattern as qap_p/config.py (Phase 1) and rcap_p/config.py (Phase 3):
every tunable documented against the specific Eternal_Master_Build_Plan.md
line it implements.
"""
import os
from dataclasses import dataclass


@dataclass
class Config:
    # --- Data / audit ---
    data_dir: str = os.environ.get("PHASE4_DATA_DIR", "./data")
    ledger_db_path: str = ""
    consent_db_path: str = ""
    memory_db_path: str = ""
    aether_db_path: str = ""

    # --- Voice pipeline ---
    # openWakeWord's real trigger is a trained ONNX classifier; this
    # sandbox has neither network access to its model host nor, as
    # discovered during Phase 4 development, the disk budget for the
    # onnxruntime stack (see voice/wake_word.py). The RMS-energy threshold
    # below drives the real (if crude) DSP fallback that stands in for it.
    wake_word_phrase: str = "hey eternal"
    wake_word_rms_threshold: float = float(os.environ.get("PHASE4_WAKE_RMS_THRESHOLD", 500.0))
    wake_word_min_voiced_frames: int = int(os.environ.get("PHASE4_WAKE_MIN_FRAMES", 3))

    # Whisper/STT and ECAPA-TDNN speaker verification both need model
    # weights this sandbox cannot fetch (see voice/stt.py, voice/speaker_verify.py
    # module docstrings) — real interfaces, fixture-backed test doubles.
    stt_min_confidence_for_action: float = float(os.environ.get("PHASE4_STT_MIN_CONF", 0.55))
    speaker_verify_min_score: float = float(os.environ.get("PHASE4_SPEAKER_MIN_SCORE", 0.70))

    # --- TTS / data classification (build plan Section 6.3, point 3) ---
    tts_local_voice: str = os.environ.get("PHASE4_TTS_VOICE", "en")

    # --- CONSENT (build plan Section 5.2: "Fine-grained per-tool consent
    # instead of all-or-nothing") ---
    consent_required_for_write: bool = True
    consent_required_for_sensitive: bool = True

    # --- ATTRIBUTE-style repeat-offender posture reused for consent
    # denials isn't needed here; Phase 4 relies on RCAP-P's own ATTRIBUTE
    # (Phase 3) for that once a call reaches the gateway. ---

    def __post_init__(self):
        os.makedirs(self.data_dir, exist_ok=True)
        self.ledger_db_path = self.ledger_db_path or os.path.join(self.data_dir, "phase4_ledger.db")
        self.consent_db_path = self.consent_db_path or os.path.join(self.data_dir, "consent.db")
        self.memory_db_path = self.memory_db_path or os.path.join(self.data_dir, "memory_facts.db")
        self.aether_db_path = self.aether_db_path or os.path.join(self.data_dir, "aether_mirror.db")
