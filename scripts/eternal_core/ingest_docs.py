#!/usr/bin/env python3
"""
Ingests every document under config.docs_dir into the persistent Chroma
store: load -> chunk -> fit embeddings -> index.

Run this once before starting the API, and again any time docs/ changes.
This is a deliberately CLI-only, operator-run step — Phase 2's network API
(eternal_core/api.py) exposes no endpoint that triggers re-ingestion
remotely. See README, "Why there's no /ingest endpoint", for why: adding
documents to memory is a write-shaped action, and the build plan's own
design (Section 6.3, point 4) says memory writes should go through RCAP-P's
INSPECT gate before landing — which doesn't exist until Phase 3. Keeping
ingestion local-operator-only for now means Phase 2 never has to fudge that
boundary.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eternal_core.config import Config
from eternal_core.rag.loaders import load_documents
from eternal_core.rag.chunking import chunk_documents
from eternal_core.rag.embeddings import configured_embedding_provider
from eternal_core.rag.store import DocStore


def main():
    cfg = Config()

    print(f"Loading documents from {cfg.docs_dir} ...")
    docs = load_documents(cfg.docs_dir)
    if not docs:
        print(f"No documents found in {cfg.docs_dir} — nothing to ingest.")
        sys.exit(1)
    for d in docs:
        print(f"  - {d.title} ({len(d.text)} chars)")

    print("Chunking ...")
    chunks = chunk_documents(docs, cfg.chunk_size_chars, cfg.chunk_overlap_chars)
    print(f"  {len(chunks)} chunks from {len(docs)} documents")

    print(f"Building index at {cfg.chroma_persist_dir} ...")
    store = DocStore(cfg.chroma_persist_dir, configured_embedding_provider())
    count = store.rebuild(chunks)
    print(f"Indexed {count} chunks. Done.")


if __name__ == "__main__":
    main()
