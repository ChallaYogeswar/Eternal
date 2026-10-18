"""
Assistant — the single object api.py, demo_exit_criteria.py, and tests all
construct from a Config. Wires: providers -> retriever -> ledger -> compiled
graph, and owns the (deliberately ephemeral, in-memory-only) per-session
conversation history.

Conversation history and "no writes": Eternal_Master_Build_Plan.md Section
5.1 lists "persistent, structured, inspectable/editable long-term memory"
as a baseline feature, but that's realized properly in Phase 4+, once
RCAP-P's INSPECT ring (Phase 3) exists to gate memory writes the same way
it gates live tool calls (Section 6.3, point 4: "No exception for 'it's
just going into memory.'"). Writing chat history to disk *before* that gate
exists would be exactly the kind of long-lived, ungated write the build
plan is warning against. So Phase 2 keeps history in a plain in-process
dict, per session_id, capped in length, gone the moment the process
restarts — real multi-turn context within a session, zero persistence.
"""
import time
from typing import Dict, List, Optional

from .config import Config
from .audit import Ledger
from .llm.ollama_provider import OllamaProvider
from .llm.cloud_provider import AnthropicProvider, OpenAIProvider
from .rag.embeddings import configured_embedding_provider
from .rag.store import DocStore
from .rag.retriever import Retriever
from .graph.build_graph import build_graph

HISTORY_CAP_TURNS = 10  # 10 user+assistant turn pairs kept per session


class IndexNotBuiltError(RuntimeError):
    pass


class Assistant:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.ledger = Ledger(self.config.ledger_db_path)

        embedding_provider = configured_embedding_provider()
        doc_store = DocStore(self.config.chroma_persist_dir, embedding_provider)
        try:
            self.retriever = Retriever(doc_store)
        except Exception as e:
            raise IndexNotBuiltError(
                f"No document index found at {self.config.chroma_persist_dir}. "
                f"Run `python scripts/ingest_docs.py` first. (underlying error: {e})"
            ) from e

        self.ollama = OllamaProvider(
            self.config.ollama_host, self.config.ollama_model, self.config.ollama_connect_timeout_seconds
        )

        provider_map = {
            "anthropic": AnthropicProvider(
                self.config.anthropic_api_key,
                self.config.anthropic_model,
                self.config.anthropic_max_tokens,
                self.config.cloud_request_timeout_seconds,
            ),
            "openai": OpenAIProvider(
                self.config.openai_api_key,
                self.config.openai_model,
                self.config.openai_max_tokens,
                self.config.cloud_request_timeout_seconds,
            ),
        }
        self.cloud_providers = [provider_map[name] for name in self.config.cloud_provider_order if name in provider_map]

        self.graph = build_graph(
            self.retriever, self.ollama, self.cloud_providers, self.ledger, self.config.retrieval_top_k
        )

        self._history: Dict[str, List[Dict[str, str]]] = {}

    def chat(self, session_id: str, message: str) -> dict:
        history = self._history.get(session_id, [])
        initial_state = {
            "session_id": session_id,
            "user_message": message,
            "history": history,
            "retrieved_chunks": [],
            "response": None,
            "provider_used": None,
            "degraded": False,
            "attempts": [],
            "started_at": time.time(),
        }
        result = self.graph.invoke(initial_state)

        updated_history = history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": result["response"]},
        ]
        self._history[session_id] = updated_history[-(HISTORY_CAP_TURNS * 2) :]

        return result

    def reset_session(self, session_id: str) -> None:
        self._history.pop(session_id, None)

    def health(self) -> dict:
        return {
            "ollama_reachable": self.ollama.health_check(),
            "ollama_model": self.config.ollama_model,
            "cloud_providers_configured": [p.name for p in self.cloud_providers if p.is_configured()],
            "cloud_providers_order": [p.name for p in self.cloud_providers],
            "ledger_entries": self.ledger.count(),
            "ledger_chain_intact": self.ledger.verify_chain()[0],
        }
