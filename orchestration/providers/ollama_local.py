"""Local Ollama provider for validation and (later) internal-data agents.

Why a second Ollama client next to `eternal_core.llm.ollama_provider`: that one uses a 5-second total timeout
(fine for a health probe, fatal for a 4B model on a 4 GB GPU that must first load into VRAM), cannot ask for JSON-
constrained output, cannot set the context window or output cap, and does not report token usage. This one can.

`format="json"` makes Ollama constrain decoding to syntactically valid JSON. That removes the commonest small-model
failure (prose around the JSON, broken syntax) but NOT wrong shapes; the runtime's strict parser and bounded
repair loop still judge the shape. Live behaviour is not verified from the build sandbox (no Ollama here)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from eternal_core.llm.base import LLMProvider, LLMResponseError, LLMResult, LLMUnavailableError

from ..llm_errors import LLMBadOutput
from .base import HttpPost, UsageResult, default_http_post, raise_for_status, timed


class OllamaLocalProvider(LLMProvider):
    name = "ollama"

    def __init__(self, model: str, host: str = "http://localhost:11434", *, timeout_seconds: float = 240.0,
                 max_output_tokens: int = 512, num_ctx: int = 4096, temperature: float = 0.2,
                 keep_alive: str = "10m", json_mode: bool = True, think: Optional[bool] = None,
                 http_post: Optional[HttpPost] = None):
        self.model, self.host = model, host.rstrip("/")
        self.timeout, self.max_output_tokens, self.num_ctx = timeout_seconds, max_output_tokens, num_ctx
        self.temperature, self.keep_alive, self.json_mode, self.think = temperature, keep_alive, json_mode, think
        self._post = http_post or default_http_post

    def is_configured(self) -> bool:
        return bool(self.host and self.model)

    def generate(self, system_prompt: str, messages: List[Dict[str, str]]) -> LLMResult:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}]
                        + [{"role": m["role"], "content": m["content"]} for m in messages],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx, "num_predict": self.max_output_tokens},
        }
        if self.json_mode:
            payload["format"] = "json"
        if self.think is not None:
            payload["think"] = self.think          # reasoning models (e.g. qwen3): False keeps the budget for the JSON
        resp, latency = timed(lambda: self._post(f"{self.host}/api/chat", {"Content-Type": "application/json"},
                                                 payload, self.timeout))
        if resp.status == 404:                      # model not pulled: tell the person the exact fix
            raise LLMResponseError(f"ollama: model '{self.model}' not found. Run: ollama pull {self.model}  "
                                   f"({resp.text[:160]})")
        raise_for_status("ollama", resp)
        try:
            data = resp.json()
            text = data["message"]["content"]
        except (ValueError, KeyError, TypeError) as e:
            raise LLMResponseError(f"ollama response missing expected fields: {e}") from e
        if not isinstance(text, str) or not text.strip():
            raise LLMBadOutput(f"ollama returned an empty completion (done_reason={data.get('done_reason')}, "
                               f"eval_count={data.get('eval_count')})", f"[empty reply: {data.get('done_reason')}]")
        return UsageResult(text=text, provider=self.name, model=self.model, latency_seconds=latency,
                           input_tokens=data.get("prompt_eval_count"), output_tokens=data.get("eval_count"))


def list_ollama_models(host: str = "http://localhost:11434", http_get=None, timeout: float = 10.0) -> List[Dict[str, Any]]:
    """Models already pulled locally (GET /api/tags)."""
    from .models import default_http_get
    get = http_get or default_http_get
    resp = get(f"{host.rstrip('/')}/api/tags", {}, timeout)
    if resp.status != 200:
        raise LLMUnavailableError(f"ollama at {host} answered HTTP {resp.status}")
    try:
        rows = resp.json().get("models", [])
    except ValueError as e:
        raise LLMResponseError("ollama model list was not JSON") from e
    return sorted(({"id": m.get("name"), "size_gb": round((m.get("size") or 0) / 1e9, 2),
                    "params": (m.get("details") or {}).get("parameter_size"),
                    "quant": (m.get("details") or {}).get("quantization_level")} for m in rows if m.get("name")),
                  key=lambda r: str(r["id"]))
