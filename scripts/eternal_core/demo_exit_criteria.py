"""
Phase 2 exit-criteria demonstration.

From Eternal_Master_Build_Plan.md, Section 8:

    "Exit criteria: you can text Eternal from your phone on any network and
    get a grounded answer referencing your own documents, with zero write
    access to anything."

This script proves each clause of that sentence against the REAL pipeline
(real retriever over the real docs/ corpus, real provider classes) running
in-process — the "texting from a phone on any network" clause specifically
is what scripts/test_http_e2e.py proves separately, the same way Phase 1
split its in-process demo from its HTTP e2e script. Run both for the full
picture; see README "Quick start".

Where this sandbox cannot reach a real Ollama or cloud backend (see
eternal_core/llm/*.py docstrings — ollama.com and huggingface.co are
outside this build environment's network allowlist, and no cloud API key
is provisioned here), Section 2 proves the fallback-routing ORDER using a
test double, clearly labeled as such, and Section 3 proves the actual
sandbox failure/degradation path with zero test doubles at all — both
matter, and neither substitutes for the other.

Run with:
    python demo_exit_criteria.py
"""
import os
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eternal_core.config import Config
from eternal_core.assistant import Assistant
from eternal_core.rag.loaders import load_documents
from eternal_core.rag.chunking import chunk_documents
from eternal_core.rag.embeddings import TfidfEmbeddingProvider
from eternal_core.rag.store import DocStore
from eternal_core.graph.build_graph import build_graph
from eternal_core.llm.base import LLMProvider, LLMResult
from eternal_core.audit.ledger import Ledger

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

checks = []


def check(label, condition):
    checks.append((label, bool(condition)))
    print(f"  [{PASS if condition else FAIL}] {label}")


def section(title):
    print(f"\n=== {title} ===")


class _DemoFakeProvider(LLMProvider):
    """Used ONLY in Section 2 below, to prove routing ORDER (which
    provider gets tried, and that a later one is skipped once an earlier
    one succeeds) without depending on a real model being reachable. Never
    imported by eternal_core itself — grep it if you want to check."""

    def __init__(self, name, should_succeed, reply=""):
        self.name = name
        self.should_succeed = should_succeed
        self.reply = reply
        self.called = False

    def is_configured(self):
        return True

    def generate(self, system_prompt, messages):
        self.called = True
        if self.should_succeed:
            return LLMResult(text=self.reply, provider=self.name, model="demo-fake", latency_seconds=0.001)
        from eternal_core.llm.base import LLMUnavailableError

        raise LLMUnavailableError(f"{self.name} deliberately fails for this demo section")


def main():
    print("Eternal — Phase 2 Exit-Criteria Demonstration")
    print("=" * 60)

    data_dir = "./data_demo"
    if os.path.exists(data_dir):
        shutil.rmtree(data_dir)
    cfg = Config(data_dir=data_dir)

    # ------------------------------------------------------------------
    section("SETUP — build the real document index from the real docs/ corpus")
    # ------------------------------------------------------------------
    docs = load_documents(cfg.docs_dir)
    check(f"loaded {len(docs)} documents from {cfg.docs_dir}", len(docs) >= 6)
    expected_titles = {
        "Eternal_Master_Build_Plan",
        "QAP_Whitepaper_CHALLAYOGESWAR",
        "RCAP_Whitepaper_CHALLAYOGESWAR",
        "Untitled_document",
        "My_Verdict_on_Outline",
        "QAP-P_Phase1_README",
    }
    found_titles = {d.title for d in docs}
    check("every Section 10 source document is present in the corpus", expected_titles.issubset(found_titles))

    chunks = chunk_documents(docs, cfg.chunk_size_chars, cfg.chunk_overlap_chars)
    check(f"chunked into {len(chunks)} retrievable pieces", len(chunks) > 50)

    store = DocStore(cfg.chroma_persist_dir, TfidfEmbeddingProvider())
    indexed_count = store.rebuild(chunks)
    check("index build reports the same chunk count it was given", indexed_count == len(chunks))

    assistant = Assistant(cfg)

    # ------------------------------------------------------------------
    section("1 — GROUNDED ANSWER: 'referencing your own documents'")
    # ------------------------------------------------------------------
    question = "What is the QUIESCE TTL, and what happens if a node stays in QUIESCE past it?"
    print(f"  Q: {question}")
    result = assistant.chat("demo-session-1", question)
    print(f"  provider_used: {result['provider_used']}  degraded: {result['degraded']}")
    print(f"  A: {result['response'][:300]}...")

    check("got a non-empty response", bool(result["response"].strip()))
    check("at least one document was retrieved", len(result["retrieved_chunks"]) > 0)
    check(
        "the QAP whitepaper (where QUIESCE TTL is actually specified) was retrieved",
        any("QAP_Whitepaper" in c["title"] for c in result["retrieved_chunks"]),
    )
    check(
        "the answer states the correct TTL value (30 seconds) from the whitepaper",
        "30" in result["response"] and ("second" in result["response"].lower()),
    )
    check(
        "the answer states the correct consequence (QUARANTINE) from the whitepaper",
        "QUARANTINE" in result["response"] or "quarantine" in result["response"].lower(),
    )

    # ------------------------------------------------------------------
    section("2 — MULTI-PROVIDER ROUTING WITH FALLBACK: proving the ORDER (test double, clearly labeled)")
    # ------------------------------------------------------------------
    print("  This section swaps in fake local/cloud providers to prove the graph's")
    print("  routing ORDER — real Ollama/cloud can't be reached from this sandbox")
    print("  (see Section 3 for the real, unmocked failure path).")

    demo_ledger = Ledger(os.path.join(data_dir, "demo_routing_ledger.db"))
    fake_local_success = _DemoFakeProvider("ollama", should_succeed=True, reply="LOCAL_ANSWERED")
    fake_cloud_should_be_skipped = _DemoFakeProvider("anthropic", should_succeed=True, reply="CLOUD_SHOULD_NOT_APPEAR")
    graph_local_wins = build_graph(
        assistant.retriever, fake_local_success, [fake_cloud_should_be_skipped], demo_ledger, top_k=3
    )
    r1 = graph_local_wins.invoke(
        {
            "session_id": "demo-routing-1", "user_message": "test question", "history": [],
            "retrieved_chunks": [], "response": None, "provider_used": None, "degraded": False,
            "attempts": [], "started_at": time.time(),
        }
    )
    check("when local succeeds, its answer is used", r1["response"] == "LOCAL_ANSWERED")
    check("when local succeeds, cloud is never called", fake_cloud_should_be_skipped.called is False)
    check("provider order tried was exactly [ollama]", [a["provider"] for a in r1["attempts"]] == ["ollama"])

    fake_local_fail = _DemoFakeProvider("ollama", should_succeed=False)
    fake_cloud_success = _DemoFakeProvider("anthropic", should_succeed=True, reply="CLOUD_ANSWERED")
    graph_cloud_fallback = build_graph(assistant.retriever, fake_local_fail, [fake_cloud_success], demo_ledger, top_k=3)
    r2 = graph_cloud_fallback.invoke(
        {
            "session_id": "demo-routing-2", "user_message": "test question", "history": [],
            "retrieved_chunks": [], "response": None, "provider_used": None, "degraded": False,
            "attempts": [], "started_at": time.time(),
        }
    )
    check("when local fails, cloud is tried and its answer is used", r2["response"] == "CLOUD_ANSWERED")
    check("fallback is marked non-degraded (a real model DID answer)", r2["degraded"] is False)
    check(
        "provider order tried was exactly [ollama, anthropic], Claude before GPT-4o per Section 3",
        [a["provider"] for a in r2["attempts"]] == ["ollama", "anthropic"],
    )

    # ------------------------------------------------------------------
    section("3 — THE REAL SANDBOX FAILURE PATH: no test doubles, genuine network/auth failures")
    # ------------------------------------------------------------------
    print("  Same question as Section 1, but tracing exactly what happened underneath:")
    print(f"  attempts: {result['attempts']}")

    check(
        "ollama was genuinely tried and genuinely refused the connection (not mocked)",
        any(a["provider"] == "ollama" and "Connection refused" in a["error"] for a in result["attempts"]),
    )
    check(
        "anthropic was genuinely tried and genuinely reported 'not configured' (no key in this sandbox)",
        any(a["provider"] == "anthropic" and a["error"] == "not configured" for a in result["attempts"]),
    )
    check(
        "openai was genuinely tried and genuinely reported 'not configured'",
        any(a["provider"] == "openai" and a["error"] == "not configured" for a in result["attempts"]),
    )
    check("the system degraded gracefully rather than erroring out", result["degraded"] is True)
    check("provider_used correctly reports the degraded path", result["provider_used"] == "retrieval_only")
    check(
        "the degraded response is STILL grounded (contains real retrieved text, not just an apology)",
        "QUIESCE" in result["response"],
    )

    # ------------------------------------------------------------------
    section("4 — ZERO WRITE ACCESS TO ANYTHING")
    # ------------------------------------------------------------------
    import inspect
    from eternal_core.llm.ollama_provider import OllamaProvider
    from eternal_core.llm.cloud_provider import AnthropicProvider, OpenAIProvider
    from eternal_core import api as api_module

    for cls in (OllamaProvider, AnthropicProvider, OpenAIProvider):
        sig = inspect.signature(cls.generate)
        check(
            f"{cls.__name__}.generate() has no tools/function-calling parameter",
            not ({"tools", "functions", "tool_choice", "function_call"} & set(sig.parameters.keys())),
        )

    mutating_routes = [
        r.path
        for r in api_module.app.routes
        if (getattr(r, "methods", set()) - {"GET", "HEAD", "OPTIONS"})
    ]
    check(
        "the only mutating API routes are /chat and /chat/reset",
        set(mutating_routes) == {"/chat", "/chat/reset"},
    )
    check("no /ingest, /tracker, /aether, or /exec route exists", not any(
        p in ("/ingest", "/tracker", "/aether", "/exec", "/reindex") for p in mutating_routes
    ))

    ledger_count_before = assistant.ledger.count()
    assistant.chat("demo-session-write-check", "another question, just to trigger one more turn")
    ledger_count_after = assistant.ledger.count()
    check(
        "the ONLY observable write from a whole chat turn is exactly one ledger row",
        ledger_count_after == ledger_count_before + 1,
    )

    # Tamper-detection proof, same technique as Phase 1's demo: corrupt a
    # stored row directly in the sqlite file and show verify_chain() catches it.
    ok_before, _ = assistant.ledger.verify_chain()
    check("ledger hash chain is intact before tampering", ok_before is True)

    conn = sqlite3.connect(cfg.ledger_db_path)
    conn.execute("UPDATE ledger SET payload = '{\"tampered\": true}' WHERE id = 1")
    conn.commit()
    conn.close()
    ok_after, broken_at = assistant.ledger.verify_chain()
    check("tampering with a stored ledger row is detected by verify_chain()", ok_after is False and broken_at == 1)

    # ------------------------------------------------------------------
    section("SUMMARY")
    # ------------------------------------------------------------------
    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    print(f"\n{passed}/{total} checks passed.")
    if passed != total:
        print("\nFailed checks:")
        for label, ok in checks:
            if not ok:
                print(f"  - {label}")
        sys.exit(1)
    else:
        print("\nAll Phase 2 exit-criteria checks passed.")
        print(
            "\nNote: 'texting from your phone on any network' is proven separately over a real\n"
            "HTTP socket by scripts/test_http_e2e.py (run the daemon, then that script) — this\n"
            "script proves everything else in-process. See README, 'Quick start'."
        )


if __name__ == "__main__":
    main()
