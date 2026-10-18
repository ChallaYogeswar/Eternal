"""
Tests for the compiled graph's routing behavior — the actual shape of
"multi-provider LLM routing with fallback" (Build Plan Section 5.1).

Three scenarios, each proven against the REAL compiled graph (not a
reimplementation of its logic):
  1. Local (Ollama) succeeds -> cloud is never touched.
  2. Local fails, cloud succeeds -> response comes from cloud, marked
     non-degraded.
  3. Everything fails -> retrieval-only fallback engages, degraded=True,
     and the response is still grounded in retrieved chunks.

FakeSuccessProvider/FakeFailProvider are test doubles standing in for
Ollama/cloud in scenarios 1-2 only, so the graph's *routing* is proven
independent of real network conditions. Scenario 3 doubles as a
description of exactly what demo_exit_criteria.py and test_http_e2e.py
prove for real, with real (non-test-double) providers, in this sandbox.
"""
import time

from eternal_core.graph.build_graph import build_graph
from eternal_core.audit.ledger import Ledger

from tests.conftest import FakeSuccessProvider, FakeFailProvider


def _initial_state(message: str):
    return {
        "session_id": "graph-test",
        "user_message": message,
        "history": [],
        "retrieved_chunks": [],
        "response": None,
        "provider_used": None,
        "degraded": False,
        "attempts": [],
        "started_at": time.time(),
    }


def test_local_success_short_circuits_cloud(built_retriever, tmp_path):
    ollama = FakeSuccessProvider(name="ollama", reply="local answer")
    cloud_a = FakeSuccessProvider(name="cloud_should_not_be_called", reply="should not appear")
    ledger = Ledger(str(tmp_path / "ledger.db"))

    graph = build_graph(built_retriever, ollama, [cloud_a], ledger, top_k=3)
    result = graph.invoke(_initial_state("tell me about zzyxquorb"))

    assert result["response"] == "local answer"
    assert result["provider_used"] == "ollama:fake-model-v1"
    assert result["degraded"] is False
    assert ollama.call_count == 1
    assert cloud_a.call_count == 0  # never reached — local succeeded


def test_local_fail_cloud_success(built_retriever, tmp_path):
    ollama = FakeFailProvider(name="ollama")
    cloud = FakeSuccessProvider(name="anthropic", reply="cloud answer")
    ledger = Ledger(str(tmp_path / "ledger.db"))

    graph = build_graph(built_retriever, ollama, [cloud], ledger, top_k=3)
    result = graph.invoke(_initial_state("tell me about zzyxquorb"))

    assert result["response"] == "cloud answer"
    assert result["provider_used"] == "anthropic:fake-model-v1"
    assert result["degraded"] is False
    assert ollama.call_count == 1
    assert cloud.call_count == 1


def test_all_providers_fail_degrades_to_grounded_retrieval_response(built_retriever, tmp_path):
    ollama = FakeFailProvider(name="ollama")
    cloud = FakeFailProvider(name="anthropic")
    ledger = Ledger(str(tmp_path / "ledger.db"))

    graph = build_graph(built_retriever, ollama, [cloud], ledger, top_k=3)
    result = graph.invoke(_initial_state("what happens with zzyxquorb after the timeout?"))

    assert result["provider_used"] == "retrieval_only"
    assert result["degraded"] is True
    # Still grounded: the distinctive fixture term must appear in the
    # degraded response, proving it's built from real retrieved content,
    # not a canned "sorry" message.
    assert "zzyxquorb" in result["response"]
    assert "alpha" in result["response"].lower() or "Alpha" in result["response"]


def test_every_chat_turn_writes_exactly_one_ledger_entry(built_retriever, tmp_path):
    ledger = Ledger(str(tmp_path / "ledger.db"))
    ollama = FakeSuccessProvider(reply="ok")
    graph = build_graph(built_retriever, ollama, [], ledger, top_k=3)

    assert ledger.count() == 0
    graph.invoke(_initial_state("first question"))
    assert ledger.count() == 1
    graph.invoke(_initial_state("second question"))
    assert ledger.count() == 2

    ok, broken_at = ledger.verify_chain()
    assert ok is True
    assert broken_at is None


def test_ledger_entry_records_degraded_flag_and_sources(built_retriever, tmp_path):
    ledger = Ledger(str(tmp_path / "ledger.db"))
    ollama = FakeFailProvider()
    cloud = FakeFailProvider()
    graph = build_graph(built_retriever, ollama, [cloud], ledger, top_k=3)

    graph.invoke(_initial_state("zzyxquorb timeout question"))
    entry = ledger.recent(1)[0]
    assert entry["event_type"] == "chat_turn"
    assert entry["payload"]["degraded"] is True
    assert entry["payload"]["provider_used"] == "retrieval_only"
    assert "alpha" in entry["payload"]["sources_used"]


def test_structured_tool_request_uses_injected_gateway_before_generation(built_retriever, tmp_path):
    class Gateway:
        def __init__(self):
            self.calls = []

        def call_tool(self, **request):
            self.calls.append(request)
            return {"ok": True, "content": {"title": "Morning Run"}}

    gateway = Gateway()
    ollama = FakeSuccessProvider(reply="tool result summarized")
    ledger = Ledger(str(tmp_path / "ledger.db"))
    graph = build_graph(built_retriever, ollama, [], ledger, top_k=3, tool_gateway=gateway)

    state = _initial_state("log my morning run habit")
    state["tool_call"] = {
        "tool_name": "aether_log_habit",
        "arguments": {"habit_title": "Morning Run"},
        "explanation": "The user explicitly requested this habit log.",
    }
    result = graph.invoke(state)

    assert result["response"] == "tool result summarized"
    assert gateway.calls == [{
        "session_id": "graph-test",
        "tool_name": "aether_log_habit",
        "arguments": {"habit_title": "Morning Run"},
        "source_label": "eternal_graph",
        "explanation": "The user explicitly requested this habit log.",
        "confirmation_channel": "text_typed",
        "caller_kind": "owner_interactive",
        "rotate_token": None,
    }]
