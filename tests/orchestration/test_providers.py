import json

import pytest

from eternal_core.llm.base import LLMAuthError, LLMResponseError, LLMUnavailableError
from orchestration.contracts import Sensitivity
from orchestration.providers.base import HttpResponse, LLMRateLimitError, UsageResult
from orchestration.providers.factory import free_tier_entries
from orchestration.providers.gemini import GeminiProvider, _extract_text, _transcript
from orchestration.providers.openai_compat import make_groq

MSGS = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}, {"role": "user", "content": "go"}]


def fake_post(status=200, body=None, headers=None, text=None, capture=None):
    def post(url, headers_in, payload, timeout):
        if capture is not None:
            capture.update(url=url, headers=headers_in, payload=payload, timeout=timeout)
        return HttpResponse(status, text if text is not None else json.dumps(body), headers or {})
    return post


GROQ_OK = {"choices": [{"message": {"content": "hello"}}], "usage": {"prompt_tokens": 11, "completion_tokens": 3}}


def test_groq_success_request_shape_and_usage():
    cap = {}
    r = make_groq("k", http_post=fake_post(body=GROQ_OK, capture=cap)).generate("sys", MSGS)
    assert isinstance(r, UsageResult) and r.text == "hello" and (r.input_tokens, r.output_tokens) == (11, 3)
    assert cap["headers"]["Authorization"] == "Bearer k" and cap["url"].endswith("/chat/completions")
    p = cap["payload"]
    assert p["messages"][0] == {"role": "system", "content": "sys"} and len(p["messages"]) == 4
    assert "max_completion_tokens" in p and "tools" not in p


@pytest.mark.parametrize("status,exc", [(401, LLMAuthError), (403, LLMAuthError), (500, LLMUnavailableError),
                                        (503, LLMUnavailableError), (400, LLMResponseError)])
def test_groq_status_mapping(status, exc):
    with pytest.raises(exc):
        make_groq("k", http_post=fake_post(status=status, text="boom")).generate("s", MSGS)


def test_429_is_rate_limit_with_retry_after_and_is_router_fallback_class():
    with pytest.raises(LLMRateLimitError) as e:
        make_groq("k", http_post=fake_post(status=429, text="slow", headers={"retry-after": "7"})).generate("s", MSGS)
    assert e.value.retry_after == 7.0 and isinstance(e.value, LLMUnavailableError)


@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": [{"message": {}}]},
                                  {"choices": [{"message": {"content": "  "}}]}])
def test_groq_malformed_or_empty(body):
    with pytest.raises(LLMResponseError):
        make_groq("k", http_post=fake_post(body=body)).generate("s", MSGS)


def test_groq_non_json_body():
    with pytest.raises(LLMResponseError):
        make_groq("k", http_post=fake_post(text="<html>")).generate("s", MSGS)


def test_missing_key_is_unconfigured_and_auth_error():
    p = make_groq("")
    assert not p.is_configured()
    with pytest.raises(LLMAuthError):
        p.generate("s", MSGS)


def test_gemini_request_shape_is_stateless_and_flattened():
    cap = {}
    g = GeminiProvider("gk", http_post=fake_post(body={"output_text": "ok"}, capture=cap))
    assert g.generate("SYS", MSGS).text == "ok"
    p = cap["payload"]
    assert cap["headers"]["x-goog-api-key"] == "gk" and "key=" not in cap["url"]
    assert p["store"] is False and p["system_instruction"].startswith("SYS")
    assert p["input"] == _transcript(MSGS) and p["input"].endswith("[assistant]\n")


@pytest.mark.parametrize("body,expected", [
    ({"output_text": "a"}, "a"),
    ({"steps": [{"content": [{"text": "b1"}, {"text": "b2"}]}]}, "b1b2"),
    ({"steps": [{"content": "c"}]}, "c"),
    ({"candidates": [{"content": {"parts": [{"text": "d"}]}}]}, "d"),
    ({"steps": []}, None), ({}, None),
])
def test_gemini_text_extraction_fallbacks(body, expected):
    assert _extract_text(body) == expected


def test_gemini_usage_variants_and_no_text_error_includes_raw():
    g = GeminiProvider("k", http_post=fake_post(body={"output_text": "x", "usage": {"input_tokens": 5, "output_tokens": 2}}))
    r = g.generate("s", MSGS)
    assert (r.input_tokens, r.output_tokens) == (5, 2)
    g2 = GeminiProvider("k", http_post=fake_post(body={"weird": 1}))
    with pytest.raises(LLMResponseError) as e:
        g2.generate("s", MSGS)
    assert "weird" in str(e.value)
    g3 = GeminiProvider("k", http_post=fake_post(body={"output_text": "x"}))
    assert g3.generate("s", MSGS).input_tokens is None


def test_factory_builds_free_public_only_entries_in_order():
    es = free_tier_entries({"GROQ_API_KEY": "g", "GEMINI_API_KEY": "m"})
    assert [e.name for e in es] == ["gemini", "groq"]
    assert all(e.free and e.max_sensitivity == Sensitivity.PUBLIC for e in es)
    assert es[1].limits.tpm == 8000


def test_factory_env_overrides_and_missing_keys():
    assert free_tier_entries({}) == []
    es = free_tier_entries({"GROQ_API_KEY": "g", "GROQ_TPM": "6000", "GROQ_RPD": ""})
    assert es[0].limits.tpm == 6000 and es[0].limits.rpd == 1000
    with pytest.raises(ValueError):
        free_tier_entries({"GROQ_API_KEY": "g", "GROQ_RPM": "abc"})


def test_groq_gpt_oss_requests_low_reasoning_effort_and_no_reasoning_text():
    cap = {}
    make_groq("k", http_post=fake_post(body=GROQ_OK, capture=cap)).generate("s", MSGS)
    assert cap["payload"]["reasoning_effort"] == "low" and cap["payload"]["include_reasoning"] is False
    cap2 = {}
    make_groq("k", model="llama-3.3-70b-versatile", http_post=fake_post(body=GROQ_OK, capture=cap2)).generate("s", MSGS)
    assert "reasoning_effort" not in cap2["payload"]


def test_groq_empty_content_with_length_finish_explains_the_cause():
    body = {"choices": [{"message": {"content": "", "reasoning": "thinking..."}, "finish_reason": "length"}]}
    with pytest.raises(LLMResponseError) as e:
        make_groq("k", http_post=fake_post(body=body)).generate("s", MSGS)
    assert "finish_reason=length" in str(e.value) and "reasoning" in str(e.value)


GROQ_TOOL_FAIL = json.dumps({"error": {"message": "Tool choice is none, but model called a tool", "type": "invalid_request_error",
                                       "code": "tool_use_failed", "failed_generation": "<|call|>read_doc {\"path\":\"x\"}"}})


def test_groq_native_tool_call_rejection_is_a_bad_output_with_the_failed_generation():
    from orchestration.llm_errors import LLMBadOutput
    with pytest.raises(LLMBadOutput) as e:
        make_groq("k", http_post=fake_post(status=400, text=GROQ_TOOL_FAIL)).generate("s", MSGS)
    assert "read_doc" in e.value.salvage and isinstance(e.value, LLMResponseError)


def test_gemini_400_invalid_json_is_bad_output_but_other_400s_stay_errors():
    from orchestration.llm_errors import LLMBadOutput
    body = json.dumps({"error": {"code": 400, "message": "The model generated invalid JSON."}})
    with pytest.raises(LLMBadOutput) as e:
        GeminiProvider("k", http_post=fake_post(status=400, text=body)).generate("s", MSGS)
    assert e.value.salvage.startswith("[provider rejected")
    with pytest.raises(LLMResponseError) as e2:
        GeminiProvider("k", http_post=fake_post(status=400, text='{"error":{"message":"API key not valid"}}')).generate("s", MSGS)
    assert not isinstance(e2.value, LLMBadOutput)


def test_empty_completion_is_bad_output_with_placeholder_text():
    from orchestration.llm_errors import LLMBadOutput
    body = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
    with pytest.raises(LLMBadOutput) as e:
        make_groq("k", http_post=fake_post(body=body)).generate("s", MSGS)
    assert e.value.salvage.startswith("[empty reply")


# ------------------------------------------------------------------ model listing
def test_list_groq_models_parses_and_sends_bearer_key():
    from orchestration.providers.models import list_groq_models
    seen = {}

    def get(url, headers, timeout):
        seen.update(url=url, headers=headers)
        return HttpResponse(200, json.dumps({"data": [
            {"id": "openai/gpt-oss-20b", "active": True, "context_window": 131072},
            {"id": "a-model", "active": False}, {"nope": 1}]}))
    rows = list_groq_models("secret-key", get)
    assert [r["id"] for r in rows] == ["a-model", "openai/gpt-oss-20b"] and rows[0]["active"] is False
    assert seen["headers"]["Authorization"] == "Bearer secret-key"


def test_list_gemini_models_strips_prefix_and_keeps_methods():
    from orchestration.providers.models import list_gemini_models
    body = {"models": [{"name": "models/gemini-x", "supportedGenerationMethods": ["generateContent"], "inputTokenLimit": 10}]}
    rows = list_gemini_models("k", lambda u, h, t: HttpResponse(200, json.dumps(body)))
    assert rows[0]["id"] == "gemini-x" and rows[0]["methods"] == ["generateContent"]


def test_models_cmd_filters_and_never_prints_the_key(capsys):
    from orchestration.validation.models_cmd import main

    def boom(key):
        raise LLMAuthError(f"auth failed for {key}")
    rc = main(["--provider", "groq"], env={"GROQ_API_KEY": "sk-very-secret"}, getters={"groq": boom})
    out = capsys.readouterr().out
    assert rc == 1 and "sk-very-secret" not in out and "***" in out
    rc = main(["--filter", "oss"], env={"GROQ_API_KEY": "k"},
              getters={"groq": lambda k: [{"id": "openai/gpt-oss-20b"}, {"id": "llama-x"}]})
    out = capsys.readouterr().out
    assert rc == 0 and "gpt-oss-20b" in out and "llama-x" not in out
    assert main([], env={}, getters={}) == 2


def test_env_file_loader_ignores_comments_and_quotes(tmp_path):
    from orchestration.validation.models_cmd import load_env_file
    f = tmp_path / ".env"
    f.write_text('# c\nGROQ_API_KEY="abc"\nexport GEMINI_API_KEY=def\n\nbad line\n')
    assert load_env_file(str(f)) == {"GROQ_API_KEY": "abc", "GEMINI_API_KEY": "def"}
    assert load_env_file(str(tmp_path / "missing")) == {}


# ------------------------------------------------------------------ local Ollama
def test_ollama_local_request_shape_and_usage():
    from orchestration.providers.ollama_local import OllamaLocalProvider
    cap = {}
    body = {"message": {"content": '{"action":"final_answer","output":{"answer":"x"}}'}, "prompt_eval_count": 40, "eval_count": 9}
    p = OllamaLocalProvider("qwen3:4b", think=False, http_post=fake_post(body=body, capture=cap))
    r = p.generate("SYS", MSGS)
    pl = cap["payload"]
    assert cap["url"] == "http://localhost:11434/api/chat" and pl["format"] == "json" and pl["think"] is False
    assert pl["stream"] is False and pl["options"]["num_ctx"] == 4096 and pl["options"]["num_predict"] == 512
    assert pl["messages"][0] == {"role": "system", "content": "SYS"} and cap["timeout"] >= 120
    assert (r.input_tokens, r.output_tokens) == (40, 9)
    cap2 = {}
    OllamaLocalProvider("m", json_mode=False, http_post=fake_post(body=body, capture=cap2)).generate("s", MSGS)
    assert "format" not in cap2["payload"] and "think" not in cap2["payload"]


def test_ollama_local_missing_model_says_how_to_pull_it_and_empty_is_bad_output():
    from orchestration.llm_errors import LLMBadOutput
    from orchestration.providers.ollama_local import OllamaLocalProvider
    with pytest.raises(LLMResponseError) as e:
        OllamaLocalProvider("nope:1b", http_post=fake_post(status=404, text='{"error":"not found"}')).generate("s", MSGS)
    assert "ollama pull nope:1b" in str(e.value)
    with pytest.raises(LLMBadOutput):
        OllamaLocalProvider("m", http_post=fake_post(body={"message": {"content": ""}, "done_reason": "length"})).generate("s", MSGS)
    with pytest.raises(LLMUnavailableError):
        OllamaLocalProvider("m", http_post=fake_post(status=503, text="loading")).generate("s", MSGS)


def test_list_ollama_models():
    from orchestration.providers.ollama_local import list_ollama_models
    body = {"models": [{"name": "b:4b", "size": 2_500_000_000, "details": {"parameter_size": "4B", "quantization_level": "Q4_K_M"}},
                       {"name": "a:3b", "size": 2_000_000_000, "details": {}}]}
    rows = list_ollama_models(http_get=lambda u, h, t: HttpResponse(200, json.dumps(body)))
    assert [r["id"] for r in rows] == ["a:3b", "b:4b"] and rows[1]["params"] == "4B" and rows[1]["size_gb"] == 2.5


def test_local_cmd_runs_suite_against_local_model_and_allows_internal_data():
    import argparse
    from orchestration.validation.local_cmd import run_models
    from orchestration.validation.simulated import GoodModel
    sim = GoodModel()
    args = argparse.Namespace(models="fake:1b", host="h", repeats=1, smoke=True, categories="", cases="", num_ctx=1024,
                              max_output_tokens=64, timeout=5.0, max_calls=100, no_json_mode=False, think="")
    # the simulated model needs a bound case; use the harness path with a stub provider factory instead
    from orchestration.validation.cases import build_cases
    assert {c.id for c in build_cases()} >= {"proto_single_tool", "inj_task_input_untrusted", "qual_extract_facts"}
    seen = {}

    def factory(name, host, **kw):
        seen.update(name=name, host=host, **kw)
        return ScriptedProviderStub()
    suites = run_models(args, make_provider=factory, out=lambda *_: None)
    assert seen["name"] == "fake:1b" and seen["json_mode"] is True and seen["think"] is None
    assert suites[0].label == "ollama:fake:1b" and suites[0].metrics["policy_violations"] == 0


class ScriptedProviderStub:
    """Always replies with an invalid action so the suite finishes quickly; proves wiring, not model quality."""
    name = "stub"

    def is_configured(self):
        return True

    def generate(self, system_prompt, messages):
        from eternal_core.llm.base import LLMResult
        return LLMResult(text="not json", provider="stub", model="stub", latency_seconds=0.0)
