"""
Chunking — splits loaded documents into retrieval-sized pieces.

Strategy: split on markdown headers first (keeps a section's heading
attached to its own content, which matters a lot for documents shaped like
the whitepapers and the build plan — a chunk of "## 3.2 The QUIESCE
Mechanism" text is far more useful standalone than an arbitrary 900-char
slice with no idea what section it's from). Any resulting section still
longer than chunk_size_chars gets a sliding-window split with overlap so no
single retrieved chunk is enormous.

This is a small, dependency-free splitter rather than pulling in the full
LangChain text-splitter ecosystem — Phase 2 already depends on `langgraph`
for orchestration; it doesn't need the rest of `langchain` just to cut
strings into pieces.
"""
import re
from dataclasses import dataclass
from typing import List

from .loaders import LoadedDocument

_HEADER_RE = re.compile(r"^(#{1,6})\s+.*$", re.MULTILINE)


@dataclass
class Chunk:
    chunk_id: str
    source: str
    title: str
    section: str
    text: str


def _split_into_sections(text: str) -> List[str]:
    """Split on markdown headers, keeping each header with the text that
    follows it. Falls back to the whole text as one 'section' if no headers
    are found (e.g. a plain .txt note)."""
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        return [text]

    sections = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append(text[start:end])

    # Anything before the first header (e.g. a title/preamble block)
    if matches[0].start() > 0:
        sections.insert(0, text[: matches[0].start()])

    return [s for s in sections if s.strip()]


def _sliding_window(text: str, chunk_size: int, overlap: int) -> List[str]:
    if len(text) <= chunk_size:
        return [text]
    pieces = []
    start = 0
    step = max(1, chunk_size - overlap)
    while start < len(text):
        pieces.append(text[start : start + chunk_size])
        if start + chunk_size >= len(text):
            break
        start += step
    return pieces


def chunk_document(doc: LoadedDocument, chunk_size_chars: int, chunk_overlap_chars: int) -> List[Chunk]:
    sections = _split_into_sections(doc.text)
    chunks: List[Chunk] = []
    for section_idx, section_text in enumerate(sections):
        header_match = _HEADER_RE.match(section_text.strip())
        section_label = header_match.group(0).lstrip("#").strip() if header_match else "(preamble)"

        pieces = _sliding_window(section_text.strip(), chunk_size_chars, chunk_overlap_chars)
        for piece_idx, piece in enumerate(pieces):
            if not piece.strip():
                continue
            chunk_id = f"{doc.title}::{section_idx}::{piece_idx}"
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    source=doc.source_path,
                    title=doc.title,
                    section=section_label,
                    text=piece.strip(),
                )
            )
    return chunks


def chunk_documents(docs: List[LoadedDocument], chunk_size_chars: int, chunk_overlap_chars: int) -> List[Chunk]:
    all_chunks: List[Chunk] = []
    for doc in docs:
        all_chunks.extend(chunk_document(doc, chunk_size_chars, chunk_overlap_chars))
    return all_chunks
