"""
Cloud providers — "Claude / GPT-4o as cloud fallback for harder calls"
(Eternal_Master_Build_Plan.md Section 3).

Both clients below make real HTTP calls to the real provider APIs (Anthropic
Messages API, OpenAI Chat Completions API) with correct request/response
handling. What differs between them in *this* build environment:

  - api.anthropic.com is reachable from this sandbox's network policy, but
    no ANTHROPIC_API_KEY is provisioned here, so calls fail with a genuine
    LLMAuthError (401/403) rather than a network error.
  - api.openai.com is outside this sandbox's network allowlist entirely
    (confirmed via the same host_not_allowed pattern as ollama.com), so
    calls fail with LLMUnavailableError here regardless of any key. On the
    actual mini-PC deployment (normal internet access, no such allowlist)
    this same code reaches OpenAI exactly as written.

Both failure modes are handled the same way by the router: "this provider
didn't work, try the next one" — the distinction between LLMAuthError and
LLMUnavailableError exists for /health reporting and troubleshooting, not
for routing logic.
"""
import time
from typing import List, Dict

import httpx

from .base import LLMProvider, LLMResult, LLMUnavailableError, LLMAuthError, LLMResponseError


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    API_URL = "https://api.anthropic.com/v1/messages"
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, api_key: str, model: str, max_tokens: int = 1024, timeout_seconds: float = 20.0):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def generate(self, system_prompt: str, messages: List[Dict[str, str]]) -> LLMResult:
        if not self.is_configured():
            raise LLMAuthError("ANTHROPIC_API_KEY is not set")

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system_prompt,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        }

        start = time.time()
        try:
            resp = httpx.post(self.API_URL, headers=headers, json=payload, timeout=self.timeout_seconds)
        except httpx.HTTPError as e:
            raise LLMUnavailableError(f"anthropic api unreachable: {e}") from e
        latency = time.time() - start

        if resp.status_code in (401, 403):
            raise LLMAuthError(f"anthropic auth failed: HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code != 200:
            raise LLMResponseError(f"anthropic returned HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            data = resp.json()
            text = "".join(
                block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
            )
        except ValueError as e:
            raise LLMResponseError(f"anthropic response was not valid JSON: {e}") from e

        if not text.strip():
            raise LLMResponseError("anthropic returned no text content")

        return LLMResult(text=text, provider=self.name, model=self.model, latency_seconds=latency)


class OpenAIProvider(LLMProvider):
    name = "openai"
    API_URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str, model: str, max_tokens: int = 1024, timeout_seconds: float = 20.0):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def generate(self, system_prompt: str, messages: List[Dict[str, str]]) -> LLMResult:
        if not self.is_configured():
            raise LLMAuthError("OPENAI_API_KEY is not set")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": system_prompt}]
            + [{"role": m["role"], "content": m["content"]} for m in messages],
        }

        start = time.time()
        try:
            resp = httpx.post(self.API_URL, headers=headers, json=payload, timeout=self.timeout_seconds)
        except httpx.HTTPError as e:
            raise LLMUnavailableError(f"openai api unreachable: {e}") from e
        latency = time.time() - start

        if resp.status_code in (401, 403):
            raise LLMAuthError(f"openai auth failed: HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code != 200:
            raise LLMResponseError(f"openai returned HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as e:
            raise LLMResponseError(f"openai response missing expected fields: {e}") from e

        if not text or not text.strip():
            raise LLMResponseError("openai returned an empty completion")

        return LLMResult(text=text, provider=self.name, model=self.model, latency_seconds=latency)
