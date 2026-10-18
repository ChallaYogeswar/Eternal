"""List the models a key can actually use (GET). Lives in providers/ because that package is the only place
allowed to touch the network. The key is sent as a header and is never returned or printed."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from eternal_core.llm.base import LLMResponseError

from .base import HttpResponse, raise_for_status

HttpGet = Callable[[str, Dict[str, str], float], HttpResponse]

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"


def default_http_get(url: str, headers: Dict[str, str], timeout: float) -> HttpResponse:
    import httpx
    from eternal_core.llm.base import LLMUnavailableError
    try:
        r = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise LLMUnavailableError(f"unreachable: {type(e).__name__}: {e}") from e
    return HttpResponse(r.status_code, r.text, {k.lower(): v for k, v in r.headers.items()})


def _json(name: str, resp: HttpResponse) -> Dict[str, Any]:
    raise_for_status(name, resp)
    try:
        data = resp.json()
    except ValueError as e:
        raise LLMResponseError(f"{name} model list was not JSON") from e
    if not isinstance(data, dict):
        raise LLMResponseError(f"{name} model list had an unexpected shape")
    return data


def list_groq_models(api_key: str, http_get: Optional[HttpGet] = None, timeout: float = 20.0) -> List[Dict[str, Any]]:
    get = http_get or default_http_get
    data = _json("groq", get(GROQ_MODELS_URL, {"Authorization": f"Bearer {api_key}"}, timeout))
    rows = [{"id": m.get("id"), "active": m.get("active", True), "context": m.get("context_window"),
             "max_output": m.get("max_completion_tokens"), "owner": m.get("owned_by")}
            for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
    return sorted(rows, key=lambda r: str(r["id"]))


def list_gemini_models(api_key: str, http_get: Optional[HttpGet] = None, timeout: float = 20.0) -> List[Dict[str, Any]]:
    get = http_get or default_http_get
    data = _json("gemini", get(GEMINI_MODELS_URL, {"x-goog-api-key": api_key}, timeout))
    rows = []
    for m in data.get("models", []):
        if not isinstance(m, dict) or not m.get("name"):
            continue
        rows.append({"id": str(m["name"]).removeprefix("models/"), "active": True,
                     "context": m.get("inputTokenLimit"), "max_output": m.get("outputTokenLimit"),
                     "methods": m.get("supportedGenerationMethods", [])})
    return sorted(rows, key=lambda r: r["id"])
