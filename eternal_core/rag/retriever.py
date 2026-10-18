"""
Retriever — the query-facing side of RAG. Wraps a DocStore's collection and
turns a raw question into a ranked list of RetrievedChunk objects that the
LangGraph nodes (graph/nodes.py) fold into the system prompt.
"""
from dataclasses import dataclass
from typing import List

from .store import DocStore


@dataclass
class RetrievedChunk:
    text: str
    source: str
    title: str
    section: str
    distance: float


class Retriever:
    def __init__(self, doc_store: DocStore):
        self._collection = doc_store.load_existing()

    def query(self, question: str, top_k: int = 5) -> List[RetrievedChunk]:
        if not question or not question.strip():
            return []

        raw = self._collection.query(query_texts=[question], n_results=top_k)

        documents = raw.get("documents", [[]])[0]
        metadatas = raw.get("metadatas", [[]])[0]
        distances = raw.get("distances", [[]])[0]

        results: List[RetrievedChunk] = []
        for text, meta, dist in zip(documents, metadatas, distances):
            results.append(
                RetrievedChunk(
                    text=text,
                    source=meta.get("source", "unknown"),
                    title=meta.get("title", "unknown"),
                    section=meta.get("section", "unknown"),
                    distance=float(dist),
                )
            )
        return results
