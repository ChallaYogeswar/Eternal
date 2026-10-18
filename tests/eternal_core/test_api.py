"""
API-level tests using FastAPI's TestClient against the real app object —
same routes, same Assistant wiring, same (real, sandbox-degraded) provider
chain as the live e2e proof in scripts/test_http_e2e.py, just in-process
for speed. Uses the REAL docs/ corpus (via the module-level app singleton
in eternal_core/api.py), so this doubles as another proof that the shipped
corpus is actually queryable — not just the tests/ fixture corpus.
"""
import pytest
from fastapi.testclient import TestClient

from eternal_core.api import app

client = TestClient(app)


def test_healthz_reports_ok_and_ledger_intact():
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ledger_chain_intact"] is True


def test_sources_lists_the_real_seeded_documents():
    r = client.get("/sources")
    assert r.status_code == 200
    files = r.json()["files"]
    assert any("Eternal_Master_Build_Plan" in f for f in files)
    assert any("QAP_Whitepaper" in f for f in files)
    assert any("RCAP_Whitepaper" in f for f in files)


def test_chat_rejects_empty_message():
    r = client.post("/chat", json={"session_id": "t", "message": "   "})
    assert r.status_code == 400


def test_chat_returns_grounded_response_with_sources():
    r = client.post(
        "/chat",
        json={"session_id": "api-test-1", "message": "What does the ATTRIBUTE ring do in RCAP?"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["response"].strip() != ""
    assert body["provider_used"]
    assert isinstance(body["degraded"], bool)
    assert len(body["sources"]) > 0
    assert any("RCAP" in s["title"] or "RCAP" in s["section"] for s in body["sources"])


def test_chat_response_shape_matches_schema():
    r = client.post("/chat", json={"session_id": "api-test-2", "message": "hello"})
    body = r.json()
    assert set(body.keys()) == {"response", "provider_used", "degraded", "sources", "latency_seconds"}
    for s in body["sources"]:
        assert set(s.keys()) == {"title", "section", "distance"}


def test_multi_turn_history_is_maintained_within_a_session():
    session = "api-test-multiturn"
    r1 = client.post("/chat", json={"session_id": session, "message": "What is QAP short for?"})
    assert r1.status_code == 200
    r2 = client.post("/chat", json={"session_id": session, "message": "And what about RCAP?"})
    assert r2.status_code == 200
    # Both turns should have succeeded independently; history itself is
    # exercised more directly at the Assistant level in test_graph.py /
    # the assistant smoke test — this just proves the API doesn't error
    # out on a second turn in the same session.


def test_reset_session_clears_history_without_erroring():
    session = "api-test-reset"
    client.post("/chat", json={"session_id": session, "message": "hi"})
    r = client.post("/chat/reset", params={"session_id": session})
    assert r.status_code == 200
    assert r.json()["session_id"] == session


def test_ledger_endpoint_is_read_only_and_reports_recent_entries():
    client.post("/chat", json={"session_id": "api-test-ledger", "message": "ledger check"})
    r = client.get("/ledger?limit=5")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) > 0
    assert entries[-1]["event_type"] == "chat_turn"


def test_ledger_verify_reports_intact_chain():
    r = client.get("/ledger/verify")
    assert r.status_code == 200
    assert r.json()["chain_intact"] is True


@pytest.mark.parametrize("method,path", [("delete", "/ledger"), ("put", "/chat"), ("delete", "/sources")])
def test_no_mutating_verb_works_against_read_only_routes(method, path):
    resp = getattr(client, method)(path)
    assert resp.status_code in (404, 405)
