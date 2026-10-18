"""Gemini provider (Interactions API, stateless).

Google's current docs (fetched 2026-10) show `POST /v1beta/interactions` with `x-goog-api-key`, a top-level
`system_instruction`, `input`, and `generation_config`. Server-side history (`previous_interaction_id`) is
NOT used: Eternal keeps its own history, so the conversation is flattened into one transcript string and
`store: false` asks Google not to retain the interaction. This flattening is a deliberate simplification
(the stateless step-array form requires replaying model steps byte-for-byte, which we do not keep).

NOT verified live from the build sandbox: the exact `store`/`generation_config.max_output_tokens` field names
and the response layout were taken from docs, with two fallbacks in `_extract_text`. Run the validation
harness `--smoke` first; it prints the raw response if parsing fails."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from eternal_core.llm.base import LLMAuthError, LLMProvider, LLMResponseError, LLMResult

from .base import HttpPost, UsageResult, default_http_post, raise_for_status, timed

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
_TRANSCRIPT_NOTE = ("\n\nThe conversation is supplied as a transcript with [user] / [assistant] labels. "
                    "Reply with ONLY the next assistant message, with no label.")


def _transcript(messages: List[Dict[str, str]]) -> str:
    parts = [f"[{m['role']}]\n{m['content']}" for m in messages]
    return "\n\n".join(parts) + "\n\n[assistant]\n"


def _extract_text(data: Dict[str, Any]) -> Optional[str]:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    for step in reversed(data.get("steps") or []):
        content = step.get("content") if isinstance(step, dict) else None
        if isinstance(content, str) and content.strip():
            return content
        texts = [c.get("text", "") for c in (content or []) if isinstance(c, dict) and c.get("text")]
        if texts:
            return "".join(texts)
    for cand in data.get("candidates") or []:               # legacy generateContent-shaped fallback
        parts = (cand.get("content") or {}).get("parts") or []
        texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
        if any(texts):
            return "".join(texts)
    return None


def _extract_usage(data: Dict[str, Any]):
    u = data.get("usage") or data.get("usage_metadata") or data.get("usageMetadata") or {}
    pick = lambda *keys: next((u[k] for k in keys if isinstance(u.get(k), int)), None)
    return (pick("input_tokens", "prompt_token_count", "promptTokenCount", "total_input_tokens"),
            pick("output_tokens", "candidates_token_count", "candidatesTokenCount", "total_output_tokens"))


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-3.5-flash-lite", *, max_output_tokens: int = 1024,
                 temperature: float = 0.2, timeout_seconds: float = 45.0, url: str = GEMINI_URL,
                 http_post: Optional[HttpPost] = None):
        self.api_key, self.model, self.url = api_key, model, url
        self.max_output_tokens, self.temperature, self.timeout = max_output_tokens, temperature, timeout_seconds
        self._post = http_post or default_http_post

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def generate(self, system_prompt: str, messages: List[Dict[str, str]]) -> LLMResult:
        if not self.is_configured():
            raise LLMAuthError("GEMINI_API_KEY is not set")
        payload = {
            "model": self.model,
            "system_instruction": system_prompt + _TRANSCRIPT_NOTE,
            "input": _transcript(messages),
            "store": False,
            "generation_config": {"temperature": self.temperature, "max_output_tokens": self.max_output_tokens},
        }
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        resp, latency = timed(lambda: self._post(self.url, headers, payload, self.timeout))
        raise_for_status(self.name, resp)
        try:
            data = resp.json()
        except ValueError as e:
            raise LLMResponseError(f"gemini response was not JSON: {resp.text[:200]}") from e
        text = _extract_text(data) if isinstance(data, dict) else None
        if not text or not text.strip():
            raise LLMResponseError(f"gemini response had no text; raw: {resp.text[:300]}")
        tin, tout = _extract_usage(data)
        return UsageResult(text=text, provider=self.name, model=self.model, latency_seconds=latency,
                           input_tokens=tin, output_tokens=tout)
