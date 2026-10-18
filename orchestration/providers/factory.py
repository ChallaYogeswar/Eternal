"""Composition-root helper: build `CloudEntry` objects from environment variables.

Free tiers (Gemini, Groq) are given a PUBLIC data ceiling: the provider may retain/use free-tier inputs, so
only public-labelled context may reach them (decision recorded in the plan, 2026-10). Paid providers are
added later with a higher ceiling and real prices. Limits below are conservative defaults; override them
with the limits shown in your own provider console."""
from __future__ import annotations

import os
from typing import List, Mapping, Optional

from ..contracts import Sensitivity
from ..model_policy import CloudEntry, RateLimits
from .base import HttpPost
from .gemini import GeminiProvider
from .openai_compat import make_groq

DEFAULT_GROQ_LIMITS = RateLimits(rpm=30, rpd=1000, tpm=8000, tpd=200000)
DEFAULT_GEMINI_LIMITS = RateLimits(rpm=10, rpd=250, tpm=250000)       # placeholder: check AI Studio


def _int(env: Mapping[str, str], key: str, default: Optional[int]) -> Optional[int]:
    v = env.get(key)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError:
        raise ValueError(f"{key} must be an integer, got {v!r}")


def _limits(env: Mapping[str, str], prefix: str, d: RateLimits) -> RateLimits:
    return RateLimits(rpm=_int(env, f"{prefix}_RPM", d.rpm), rpd=_int(env, f"{prefix}_RPD", d.rpd),
                      tpm=_int(env, f"{prefix}_TPM", d.tpm), tpd=_int(env, f"{prefix}_TPD", d.tpd))


def free_tier_entries(env: Optional[Mapping[str, str]] = None, *, http_post: Optional[HttpPost] = None,
                      max_output_tokens: int = 1024) -> List[CloudEntry]:
    """Entries for every free provider whose key is present, in preference order (Gemini, then Groq)."""
    env = os.environ if env is None else env
    out: List[CloudEntry] = []
    if env.get("GEMINI_API_KEY"):
        p = GeminiProvider(env["GEMINI_API_KEY"], env.get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
                           max_output_tokens=max_output_tokens, http_post=http_post)
        out.append(CloudEntry(p, Sensitivity.PUBLIC, 0.0, 0.0, max_output_tokens,
                              _limits(env, "GEMINI", DEFAULT_GEMINI_LIMITS), free=True))
    if env.get("GROQ_API_KEY"):
        p = make_groq(env["GROQ_API_KEY"], env.get("GROQ_MODEL", "openai/gpt-oss-20b"),
                      max_output_tokens=max_output_tokens, http_post=http_post)
        out.append(CloudEntry(p, Sensitivity.PUBLIC, 0.0, 0.0, max_output_tokens,
                              _limits(env, "GROQ", DEFAULT_GROQ_LIMITS), free=True))
    return out
