"""Honest labels for what each integration is really connected to (audit E-06 / E-07 / E-05).

Pure function of the environment; imports no server module (they spawn stores on import). Daemons put the result in
/healthz so a client or screen cannot present a local mirror, a simulation or an offline stub as the real thing.
"""
import os
from typing import Dict, Mapping, Optional


def integration_modes(env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    env = os.environ if env is None else env
    aether_http = env.get("AETHER_BACKEND", "sqlite").casefold() == "http" and bool(env.get("AETHER_API_URL")) \
        and bool(env.get("AETHER_API_TOKEN"))
    ha_real = env.get("PHASE5_HA_REAL") == "1" or (bool(env.get("HOME_ASSISTANT_BASE_URL"))
                                                   and bool(env.get("HOME_ASSISTANT_TOKEN")))
    return {
        "aether": "live_http_api" if aether_http else "LOCAL_SQLITE_MIRROR (NOT synced with the real ÆTHER app)",
        "study_tracker": "LOCAL_SQLITE_MIRROR (NOT synced with a real study tracker)",
        "cinemavault": "LOCAL_SQLITE_MIRROR (NOT connected to the real CinemaVault)",
        "web_search": "OFFLINE_STUB (synthetic results; NOT live internet search)",
        "home_assistant": "real_rest_client_configured (not live-tested here)" if ha_real
                          else "SIMULATED (no Home Assistant configured)",
        "laptop_agent": "real_os_provider (unavailable on this build)" if env.get("PHASE5_LAPTOP_REAL") == "1"
                        else "SIMULATED (no real OS actions are performed)",
        "remote_control": "PLACEHOLDER (no remote-desktop tool selected; connection id is a stub)",
    }
