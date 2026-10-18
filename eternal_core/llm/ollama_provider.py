"""
Ollama provider — the "3B-class local model as the default" from
Eternal_Master_Build_Plan.md Section 3.

This is a real HTTP client against Ollama's /api/chat endpoint (the same
endpoint the real mini-PC deployment hits) — not a stub. What Phase 2 cannot
do inside this sandboxed build environment is prove it end-to-end, because:

  1. Ollama itself can't be installed here — its site and model registry
     are outside the sandbox's network allowlist (confirmed: ollama.com
     returns a proxy-level "host_not_allowed" 403 at the network layer,
     before ever reaching Ollama's servers).
  2. Even a manually-built Ollama binary couldn't pull model weights for
     the same reason.

So: this client is correct and ready for the real mini-PC (where Ollama
*is* reachable), but in this build environment every call to it will
raise LLMUnavailableError from a genuine connection failure — which is
exactly the failure path the router/graph need to handle gracefully, and
exactly what demo_exit_criteria.py exercises for real rather than mocking.
"""
import time
from typing import List, Dict

import httpx

from .base import LLMProvider, LLMResult, LLMUnavailableError, LLMResponseError


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, host: str, model: str, connect_timeout_seconds: float = 5.0):
        self.host = host.rstrip("/")
        self.model = model
        self.connect_timeout_seconds = connect_timeout_seconds

    def is_configured(self) -> bool:
        # Ollama needs no API key — it's "configured" as long as a host/model
        # are set, which they always are (config.py supplies defaults).
        # Whether it's actually *reachable* is a generate()-time question.
        return bool(self.host and self.model)

    def health_check(self) -> bool:
        """Fast, side-effect-free reachability probe used by /health and by
        tests — does NOT participate in the fallback decision itself
        (generate() below is the source of truth for that), but lets the
        API report status without paying a full generation's latency."""
        try:
            httpx.get(f"{self.host}/api/tags", timeout=self.connect_timeout_seconds)
            return True
        except httpx.HTTPError:
            return False

    def generate(self, system_prompt: str, messages: List[Dict[str, str]]) -> LLMResult:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}] + list(messages),
            "stream": False,
        }
        start = time.time()
        try:
            resp = httpx.post(
                f"{self.host}/api/chat",
                json=payload,
                timeout=self.connect_timeout_seconds,
            )
        except httpx.HTTPError as e:
            raise LLMUnavailableError(f"ollama at {self.host} unreachable: {e}") from e

        latency = time.time() - start

        if resp.status_code != 200:
            raise LLMResponseError(f"ollama returned HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            data = resp.json()
            text = data["message"]["content"]
        except (ValueError, KeyError) as e:
            raise LLMResponseError(f"ollama response missing expected fields: {e}") from e

        if not text or not text.strip():
            raise LLMResponseError("ollama returned an empty completion")

        return LLMResult(text=text, provider=self.name, model=self.model, latency_seconds=latency)
