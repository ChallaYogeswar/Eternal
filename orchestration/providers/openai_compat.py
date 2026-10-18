"""OpenAI-compatible chat-completions provider (used for Groq; works for other compatible hosts).

Request/response shape taken from Groq's API reference (fetched 2026-10): POST {base}/chat/completions,
Bearer auth, `max_completion_tokens`, reply at choices[0].message.content, usage.prompt_tokens /
completion_tokens. Live behaviour is NOT verified from the build sandbox — run the validation
harness `--smoke` first on a machine with network access and a key."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from eternal_core.llm.base import LLMAuthError, LLMProvider, LLMResponseError, LLMResult

from ..llm_errors import LLMBadOutput

from .base import HttpPost, UsageResult, default_http_post, raise_for_status, timed


class OpenAICompatProvider(LLMProvider):
    def __init__(self, name: str, api_key: str, model: str, url: str, *, max_output_tokens: int = 1024,
                 temperature: float = 0.2, timeout_seconds: float = 30.0, http_post: Optional[HttpPost] = None,
                 extra_payload: Optional[Dict[str, Any]] = None):
        self.name = name
        self.api_key, self.model, self.url = api_key, model, url
        self.max_output_tokens, self.temperature, self.timeout = max_output_tokens, temperature, timeout_seconds
        self._post = http_post or default_http_post
        self.extra_payload = dict(extra_payload or {})

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def generate(self, system_prompt: str, messages: List[Dict[str, str]]) -> LLMResult:
        if not self.is_configured():
            raise LLMAuthError(f"{self.name} API key is not set")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}]
                        + [{"role": m["role"], "content": m["content"]} for m in messages],
            "max_completion_tokens": self.max_output_tokens,
            "temperature": self.temperature,
        }
        payload.update(self.extra_payload)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        resp, latency = timed(lambda: self._post(self.url, headers, payload, self.timeout))
        raise_for_status(self.name, resp)
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMResponseError(f"{self.name} response missing expected fields: {e}") from e
        if not isinstance(text, str) or not text.strip():
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            finish = choice.get("finish_reason")
            raise LLMBadOutput(      # the model answered with nothing usable: a protocol failure, not an outage
                f"{self.name} returned an empty completion (finish_reason={finish}, "
                f"reasoning_chars={len(str(msg.get('reasoning') or msg.get('reasoning_content') or ''))}, "
                f"usage={data.get('usage')})",
                f"[empty reply: finish_reason={finish}]")
        usage = data.get("usage") or {}
        return UsageResult(text=text, provider=self.name, model=self.model, latency_seconds=latency,
                           input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"))


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def make_groq(api_key: str, model: str = "openai/gpt-oss-20b", **kw) -> OpenAICompatProvider:
    """gpt-oss models are reasoning models: by default they spend the output budget on hidden reasoning and can
    return an empty `content` (docs: reasoning_effort low|medium|high, include_reasoning bool; both only for the
    gpt-oss models). We ask for LOW effort and no reasoning text so the JSON action fits in the budget."""
    extra = dict(kw.pop("extra_payload", None) or {})
    if model.startswith("openai/gpt-oss"):
        extra.setdefault("reasoning_effort", "low")
        extra.setdefault("include_reasoning", False)
    return OpenAICompatProvider("groq", api_key, model, GROQ_URL, extra_payload=extra, **kw)
