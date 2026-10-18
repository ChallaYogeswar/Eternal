"""
Tests for the Assistant facade itself — the piece that wires Config into a
running graph, separate from testing the graph's internal routing (that's
test_graph.py) or the HTTP surface (that's test_api.py).
"""
import pytest

from eternal_core.config import Config
from eternal_core.assistant import Assistant, IndexNotBuiltError


def test_assistant_raises_clear_error_when_index_not_built(tmp_path):
    cfg = Config(
        docs_dir=str(tmp_path / "empty_docs"),  # doesn't even exist
        data_dir=str(tmp_path / "data"),
    )
    with pytest.raises(IndexNotBuiltError) as exc_info:
        Assistant(cfg)
    assert "ingest_docs.py" in str(exc_info.value)


def test_assistant_builds_successfully_once_index_exists(tmp_path, test_docs_dir):
    from eternal_core.rag.loaders import load_documents
    from eternal_core.rag.chunking import chunk_documents
    from eternal_core.rag.embeddings import TfidfEmbeddingProvider
    from eternal_core.rag.store import DocStore

    cfg = Config(
        docs_dir=test_docs_dir,
        data_dir=str(tmp_path / "data"),
        ollama_host="http://localhost:1",
    )
    docs = load_documents(cfg.docs_dir)
    chunks = chunk_documents(docs, cfg.chunk_size_chars, cfg.chunk_overlap_chars)
    DocStore(cfg.chroma_persist_dir, TfidfEmbeddingProvider()).rebuild(chunks)

    assistant = Assistant(cfg)
    health = assistant.health()
    assert health["ollama_reachable"] is False  # unroutable host in this fixture config... actually default host
    assert "cloud_providers_configured" in health


def test_assistant_health_reports_unconfigured_cloud_providers_when_no_keys(tmp_path, test_docs_dir):
    from eternal_core.rag.loaders import load_documents
    from eternal_core.rag.chunking import chunk_documents
    from eternal_core.rag.embeddings import TfidfEmbeddingProvider
    from eternal_core.rag.store import DocStore

    cfg = Config(docs_dir=test_docs_dir, data_dir=str(tmp_path / "data"), anthropic_api_key="", openai_api_key="")
    docs = load_documents(cfg.docs_dir)
    chunks = chunk_documents(docs, cfg.chunk_size_chars, cfg.chunk_overlap_chars)
    DocStore(cfg.chroma_persist_dir, TfidfEmbeddingProvider()).rebuild(chunks)

    assistant = Assistant(cfg)
    assert assistant.health()["cloud_providers_configured"] == []


def test_assistant_chat_caps_history_length(tmp_path, test_docs_dir):
    from eternal_core.rag.loaders import load_documents
    from eternal_core.rag.chunking import chunk_documents
    from eternal_core.rag.embeddings import TfidfEmbeddingProvider
    from eternal_core.rag.store import DocStore

    cfg = Config(docs_dir=test_docs_dir, data_dir=str(tmp_path / "data"), ollama_host="http://localhost:1")
    docs = load_documents(cfg.docs_dir)
    chunks = chunk_documents(docs, cfg.chunk_size_chars, cfg.chunk_overlap_chars)
    DocStore(cfg.chroma_persist_dir, TfidfEmbeddingProvider()).rebuild(chunks)

    assistant = Assistant(cfg)
    session = "cap-test"
    for i in range(30):
        assistant.chat(session, f"question number {i}")

    # HISTORY_CAP_TURNS pairs, *2 messages per pair
    from eternal_core.assistant import HISTORY_CAP_TURNS

    assert len(assistant._history[session]) == HISTORY_CAP_TURNS * 2


def test_assistant_reset_session_clears_in_memory_history(tmp_path, test_docs_dir):
    from eternal_core.rag.loaders import load_documents
    from eternal_core.rag.chunking import chunk_documents
    from eternal_core.rag.embeddings import TfidfEmbeddingProvider
    from eternal_core.rag.store import DocStore

    cfg = Config(docs_dir=test_docs_dir, data_dir=str(tmp_path / "data"), ollama_host="http://localhost:1")
    docs = load_documents(cfg.docs_dir)
    chunks = chunk_documents(docs, cfg.chunk_size_chars, cfg.chunk_overlap_chars)
    DocStore(cfg.chroma_persist_dir, TfidfEmbeddingProvider()).rebuild(chunks)

    assistant = Assistant(cfg)
    assistant.chat("reset-test", "hello")
    assert "reset-test" in assistant._history
    assistant.reset_session("reset-test")
    assert "reset-test" not in assistant._history
