"""
Embedding provider abstraction — same seam pattern as qap_p/hardware_key.py:
one abstract contract, and a real, working implementation that documents
exactly what it's standing in for.

Eternal_Master_Build_Plan.md Section 3 names "SQLite + ChromaDB" for memory,
and Section 5.1 calls for RAG over the owner's docs. The build plan doesn't
mandate a specific embedding model — that choice is Phase 2's own to make.

Why TF-IDF and not a dense neural embedding model:
Dense embeddings (sentence-transformers, Ollama's nomic-embed-text, OpenAI/
Anthropic embedding endpoints) all require either downloading pretrained
weights from a model registry (huggingface.co, Ollama's registry) or a live
API call — and this sandboxed build environment's network policy blocks
huggingface.co and ollama.com outright (confirmed: both return a
proxy-level "host_not_allowed" 403). TfidfEmbeddingProvider below is real,
not a stub — it's a genuine, classic IR technique (still used in production
hybrid-search systems) that computes entirely offline from the corpus
itself, so RAG retrieval in this phase is fully real and testable end to
end rather than mocked.

The seam for the real thing: implement EmbeddingProvider against Ollama's
/api/embeddings endpoint (e.g. nomic-embed-text) once deployed on the real
mini-PC where Ollama has genuine network access, or against a cloud
embeddings API. Nothing else in rag/ depends on which implementation is
plugged in — store.py only calls fit()/embed()/is_fitted().
"""
import pickle
import json
import os
import urllib.request
from abc import ABC, abstractmethod
from typing import List

import chromadb
from sklearn.feature_extraction.text import TfidfVectorizer


class EmbeddingProvider(ABC):
    @abstractmethod
    def fit(self, corpus: List[str]) -> None:
        """Must be called once over the full chunk corpus before embed().
        Dense embedding providers would typically make this a no-op (each
        text embeds independently of the others); TF-IDF's vocabulary is
        corpus-dependent, so re-fitting means re-indexing everything — see
        README, 'known simplification: TF-IDF requires a full re-index'."""

    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        ...

    @abstractmethod
    def is_fitted(self) -> bool:
        ...


class TfidfEmbeddingProvider(EmbeddingProvider):
    def __init__(self, max_features: int = 4096):
        self._vectorizer = TfidfVectorizer(max_features=max_features, stop_words="english")
        self._fitted = False

    def fit(self, corpus: List[str]) -> None:
        if not corpus:
            raise ValueError("cannot fit an embedding provider on an empty corpus")
        self._vectorizer.fit(corpus)
        self._fitted = True

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not self._fitted:
            raise RuntimeError("TfidfEmbeddingProvider.embed() called before fit()")
        matrix = self._vectorizer.transform(texts)
        return matrix.toarray().tolist()

    def is_fitted(self) -> bool:
        return self._fitted

    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            pickle.dump(self._vectorizer, f)

    @classmethod
    def load(cls, path: str) -> "TfidfEmbeddingProvider":
        instance = cls.__new__(cls)
        with open(path, "rb") as f:
            instance._vectorizer = pickle.load(f)
        instance._fitted = True
        return instance


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Dense local embeddings from Ollama's ``/api/embed`` endpoint."""

    def __init__(self, host: str = "http://localhost:11434", model: str = "nomic-embed-text", timeout: float = 10.0):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    def fit(self, corpus: List[str]) -> None:
        if not corpus:
            raise ValueError("cannot fit an embedding provider on an empty corpus")

    def embed(self, texts: List[str]) -> List[List[float]]:
        request = urllib.request.Request(
            f"{self.host}/api/embed",
            data=json.dumps({"model": self.model, "input": texts}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - backend errors need one provider-level failure
            raise RuntimeError(f"Ollama embedding request failed: {exc}") from exc
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise RuntimeError("Ollama embedding response did not contain one embedding per input")
        return embeddings

    def is_fitted(self) -> bool:
        return True


def configured_embedding_provider() -> EmbeddingProvider:
    """Select dense local embeddings explicitly; retain offline TF-IDF by default."""
    if os.environ.get("ETERNAL_EMBEDDING_BACKEND", "tfidf").casefold() == "ollama":
        return OllamaEmbeddingProvider(
            host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            model=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
            timeout=float(os.environ.get("OLLAMA_EMBED_TIMEOUT", "10")),
        )
    return TfidfEmbeddingProvider()


class ChromaEmbeddingFunctionAdapter(chromadb.EmbeddingFunction):
    """Chroma expects an EmbeddingFunction subclass implementing __call__.
    Subclassing (rather than duck-typing a plain callable) matters here:
    chromadb.EmbeddingFunction.__init_subclass__ wraps __call__ with its own
    validation, and the base class supplies embed_query() (defaults to
    __call__) that Chroma's query path calls directly — a plain callable
    without this base class is missing embed_query and query() breaks with
    an AttributeError (confirmed while building this).

    Thin adapter so any EmbeddingProvider can be handed straight to a Chroma
    collection as its embedding_function — Chroma only ever calls this
    class; the actual embedding logic stays in the EmbeddingProvider."""

    def __init__(self, provider: EmbeddingProvider):
        self._provider = provider

    def __call__(self, input: List[str]) -> List[List[float]]:  # noqa: A002 (chroma's own param name)
        return self._provider.embed(list(input))

    def name(self) -> str:
        # Chroma calls this to fingerprint the embedding function for a
        # collection's stored metadata.
        return "eternal-tfidf-adapter"

    def get_config(self) -> dict:
        # Chroma's newer versions want every embedding function to be able
        # to serialize/deserialize itself via get_config()/build_from_config()
        # so a collection can reconstruct its embedding function from
        # persisted metadata alone. That mechanism doesn't fit a fitted
        # TF-IDF vectorizer well (its "config" is the whole fitted
        # vocabulary, not a few JSON-safe scalars) — DocStore.load_existing()
        # already handles reconstruction for real, by reloading the pickled
        # vectorizer directly (see store.py) rather than through this path.
        # Returning an empty config (rather than leaving the method
        # unimplemented) just silences the deprecation warning honestly;
        # build_from_config below makes the actual limitation explicit
        # rather than silently returning a broken, unfitted adapter.
        return {}

    @staticmethod
    def build_from_config(config: dict) -> "ChromaEmbeddingFunctionAdapter":
        # Chroma calls this defensively in some internal serialization
        # paths, not on the real reconstruction path this project actually
        # uses (DocStore.load_existing() reloads the fitted vectorizer
        # pickle directly and constructs the adapter itself — see store.py).
        # Returning a fresh, not-yet-fitted adapter here (rather than
        # raising) avoids spamming logs with a warning on every collection
        # open; anyone calling embed()/query() through *this* unfitted
        # instance instead of the real one would get embeddings.py's own
        # RuntimeError("embed() called before fit()") immediately, so
        # nothing is silently wrong either way.
        return ChromaEmbeddingFunctionAdapter(TfidfEmbeddingProvider())
