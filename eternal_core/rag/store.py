"""
Persistent vector store — Eternal_Master_Build_Plan.md Section 3: "SQLite
(structured facts) + ChromaDB (semantic recall)". Phase 2 is the first
phase that actually populates the ChromaDB half of that pairing (the SQLite
half, structured long-term facts, is Phase 4+ territory — see README).

One collection, one embedding function, rebuilt in full on every ingest run
(see embeddings.py's module docstring for why TF-IDF specifically means a
full re-index rather than incremental add-one-document ingestion).
"""
import os
import shutil
from typing import List, Optional

import chromadb

from .chunking import Chunk
from .embeddings import EmbeddingProvider, ChromaEmbeddingFunctionAdapter

COLLECTION_NAME = "eternal_docs"
VECTORIZER_FILENAME = "tfidf_vectorizer.pkl"


class DocStore:
    def __init__(self, persist_dir: str, embedding_provider: EmbeddingProvider):
        self.persist_dir = persist_dir
        self.embedding_provider = embedding_provider
        os.makedirs(persist_dir, exist_ok=True)
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._embedding_function = ChromaEmbeddingFunctionAdapter(embedding_provider)

    def _vectorizer_path(self) -> str:
        return os.path.join(self.persist_dir, VECTORIZER_FILENAME)

    def rebuild(self, chunks: List[Chunk]) -> int:
        """Full re-index: fit the embedding provider on the whole chunk
        corpus, then recreate the collection from scratch. Returns the
        number of chunks indexed."""
        if not chunks:
            raise ValueError("cannot build an index from zero chunks")

        texts = [c.text for c in chunks]
        self.embedding_provider.fit(texts)
        if hasattr(self.embedding_provider, "save"):
            self.embedding_provider.save(self._vectorizer_path())

        try:
            self._client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass  # collection didn't exist yet — nothing to delete

        collection = self._client.create_collection(
            name=COLLECTION_NAME, embedding_function=self._embedding_function
        )

        ids = [c.chunk_id for c in chunks]
        documents = [c.text for c in chunks]
        metadatas = [{"source": c.source, "title": c.title, "section": c.section} for c in chunks]

        # Chroma batches internally, but keep this explicit and simple —
        # Phase 2's corpus is a handful of docs, not a production-scale
        # ingestion job.
        collection.add(ids=ids, documents=documents, metadatas=metadatas)
        return len(chunks)

    def load_existing(self) -> "chromadb.Collection":
        """Reopen a previously-built collection (e.g. after a process
        restart) without re-fitting the embedding provider — reuses the
        persisted vectorizer so query vectors line up with what's already
        indexed."""
        vec_path = self._vectorizer_path()
        if hasattr(self.embedding_provider, "load") and os.path.exists(vec_path):
            loaded = type(self.embedding_provider).load(vec_path)
            self.embedding_provider = loaded
            self._embedding_function = ChromaEmbeddingFunctionAdapter(loaded)
        return self._client.get_collection(name=COLLECTION_NAME, embedding_function=self._embedding_function)

    def collection_exists(self) -> bool:
        try:
            self._client.get_collection(name=COLLECTION_NAME, embedding_function=self._embedding_function)
            return True
        except Exception:
            return False

    def wipe(self) -> None:
        """Used by tests / a clean re-ingest. Removes the persisted
        collection and vectorizer entirely."""
        try:
            self._client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        vec_path = self._vectorizer_path()
        if os.path.exists(vec_path):
            os.remove(vec_path)
