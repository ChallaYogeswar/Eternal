"""
Shared fixtures.

FakeSuccessProvider and FakeFailProvider below are test doubles, used ONLY
in this test suite to prove the graph's routing logic on the "a provider
actually succeeds" path — something this sandbox can't exercise live
against real Ollama/cloud backends (see eternal_core/llm/ollama_provider.py
and cloud_provider.py docstrings). They are NOT part of eternal_core itself
and are never wired into the real Assistant/API — grep eternal_core/ if you
want to confirm that yourself.

demo_exit_criteria.py and scripts/test_http_e2e.py, by contrast, exercise
the REAL providers against the REAL (degraded, in-this-sandbox) failure
path — no test doubles there. Between the two, both branches of the
fallback chain (success and total failure) are proven, not just one.

Module-level side effect below (not a fixture): test_api.py imports
eternal_core.api at collection time, which constructs the module-level
Assistant singleton against the REAL docs/ folder and REAL data/chroma
index — deliberately, so those tests prove the actual shipped corpus is
queryable, not just the tests/ fixture corpus. That singleton requires the
index to already be built (see assistant.py's IndexNotBuiltError). Rather
than making every contributor remember to run `scripts/ingest_docs.py`
before `pytest` works, conftest.py builds it here if it's missing —
before any test module executes its own imports. Deploying for real still
means running scripts/ingest_docs.py as an explicit step (see README) —
this is a test-suite convenience, not a statement that ingestion should
ever happen implicitly on a live deployment.
"""
import os
import shutil
import time

import pytest


def _ensure_real_index_built():
    from eternal_core.config import Config
    from eternal_core.rag.loaders import load_documents
    from eternal_core.rag.chunking import chunk_documents
    from eternal_core.rag.embeddings import TfidfEmbeddingProvider
    from eternal_core.rag.store import DocStore

    cfg = Config()
    if os.path.isdir(cfg.chroma_persist_dir) and os.listdir(cfg.chroma_persist_dir):
        return  # already built (e.g. scripts/ingest_docs.py was already run)
    docs = load_documents(cfg.docs_dir)
    chunks = chunk_documents(docs, cfg.chunk_size_chars, cfg.chunk_overlap_chars)
    DocStore(cfg.chroma_persist_dir, TfidfEmbeddingProvider()).rebuild(chunks)


_ensure_real_index_built()

from eternal_core.config import Config
from eternal_core.rag.loaders import load_documents
from eternal_core.rag.chunking import chunk_documents
from eternal_core.rag.embeddings import TfidfEmbeddingProvider
from eternal_core.rag.store import DocStore
from eternal_core.rag.retriever import Retriever
from eternal_core.audit.ledger import Ledger
from eternal_core.llm.base import LLMProvider, LLMResult, LLMUnavailableError


class FakeSuccessProvider(LLMProvider):
    """Always succeeds instantly with a fixed, recognizable string —
    lets tests assert 'the graph used a provider and stopped' without
    needing a real model."""

    def __init__(self, name="fake_success", model="fake-model-v1", reply="FAKE_PROVIDER_REPLY"):
        self.name = name
        self.model = model
        self.reply = reply
        self.call_count = 0

    def is_configured(self) -> bool:
        return True

    def generate(self, system_prompt, messages) -> LLMResult:
        self.call_count += 1
        return LLMResult(text=self.reply, provider=self.name, model=self.model, latency_seconds=0.001)


class FakeFailProvider(LLMProvider):
    """Always raises LLMUnavailableError — lets tests force the fallback
    chain to keep going without depending on network conditions."""

    def __init__(self, name="fake_fail"):
        self.name = name
        self.call_count = 0

    def is_configured(self) -> bool:
        return True

    def generate(self, system_prompt, messages):
        self.call_count += 1
        raise LLMUnavailableError(f"{self.name} deliberately fails")


class FakeUnconfiguredProvider(LLMProvider):
    """is_configured() is False — lets tests assert the router SKIPS a
    provider without ever calling generate() on it."""

    def __init__(self, name="fake_unconfigured"):
        self.name = name
        self.call_count = 0

    def is_configured(self) -> bool:
        return False

    def generate(self, system_prompt, messages):
        self.call_count += 1
        raise AssertionError("generate() should never be called on an unconfigured provider")


@pytest.fixture(scope="session")
def test_docs_dir(tmp_path_factory):
    """A tiny, fast, deterministic 3-document corpus — separate from the
    real docs/ folder so unit tests don't depend on (or slow down from)
    the full ~200-chunk real index."""
    d = tmp_path_factory.mktemp("test_docs")
    (d / "alpha.md").write_text(
        "# Alpha Protocol\n\n"
        "## Section 1: Purpose\n"
        "The Alpha Protocol governs how test nodes greet each other using the word zzyxquorb "
        "as a distinctive, ungoogleable marker term that only appears in this test fixture.\n\n"
        "## Section 2: Timeout\n"
        "Alpha handshakes expire after exactly 42 zzyxquorb-seconds if unacknowledged.\n"
    )
    (d / "beta.md").write_text(
        "# Beta Notes\n\n"
        "## Overview\n"
        "Beta is unrelated to Alpha and discusses gardening schedules for tomatoes and basil.\n"
    )
    (d / "gamma.txt").write_text(
        "Gamma is a plain text file with no headers at all, just prose about weather patterns."
    )
    return str(d)


@pytest.fixture()
def config(tmp_path, test_docs_dir):
    cfg = Config(
        docs_dir=test_docs_dir,
        data_dir=str(tmp_path / "data"),
        ollama_host="http://localhost:1",  # deliberately unroutable port, fails fast
        ollama_connect_timeout_seconds=1.0,
        anthropic_api_key="",
        openai_api_key="",
    )
    return cfg


@pytest.fixture()
def built_retriever(config):
    docs = load_documents(config.docs_dir)
    chunks = chunk_documents(docs, config.chunk_size_chars, config.chunk_overlap_chars)
    store = DocStore(config.chroma_persist_dir, TfidfEmbeddingProvider())
    store.rebuild(chunks)
    return Retriever(store)


@pytest.fixture()
def ledger(config):
    return Ledger(config.ledger_db_path)
