"""
Eternal Core (Phase 2) Configuration
-------------------------------------
Central place for every tunable this phase needs, each documented against
the specific Eternal_Master_Build_Plan.md line it implements — same pattern
as qap_p/config.py in Phase 1, so a future phase can find the reasoning
without re-deriving it.
"""
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    # --- Section 3 "Tech Stack" / Core reasoning ---
    # "Ollama, 3B-class local model as the default."
    ollama_host: str = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
    ollama_connect_timeout_seconds: float = float(os.environ.get("OLLAMA_TIMEOUT", 5))

    # "Claude / GPT-4o as cloud fallback for harder calls." Order here is
    # deliberate: Claude first, GPT-4o second, matching the build plan's own
    # ordering. Either can be individually disabled by leaving its API key
    # unset — is_configured() on each provider reflects that.
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
    anthropic_max_tokens: int = int(os.environ.get("ANTHROPIC_MAX_TOKENS", 1024))

    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    openai_model: str = os.environ.get("OPENAI_MODEL", "gpt-4o")
    openai_max_tokens: int = int(os.environ.get("OPENAI_MAX_TOKENS", 1024))

    cloud_request_timeout_seconds: float = float(os.environ.get("CLOUD_TIMEOUT", 20))

    # --- Section 5.1 baseline: "RAG over your own docs and notes —
    # including the whitepapers and this build plan, so Eternal can answer
    # questions about its own design." ---
    docs_dir: str = os.environ.get("ETERNAL_DOCS_DIR", "./docs")
    chunk_size_chars: int = int(os.environ.get("ETERNAL_CHUNK_SIZE", 900))
    chunk_overlap_chars: int = int(os.environ.get("ETERNAL_CHUNK_OVERLAP", 150))
    retrieval_top_k: int = int(os.environ.get("ETERNAL_TOP_K", 5))

    # --- Local-first storage (Section 5.1: "cloud LLM as the only external
    # dependency by default") ---
    data_dir: str = os.environ.get("ETERNAL_DATA_DIR", "./data")
    chroma_persist_dir: str = ""
    ledger_db_path: str = ""

    # --- Section 3 "Reachability": "Tailscale mesh (WireGuard)... private,
    # not internet-exposed, regardless of physical location." Phase 2 cannot
    # join a real Tailscale mesh from this sandbox (see README, "What's
    # simulated vs real"); bind_host is deliberately configurable so the real
    # mini-PC deployment binds to the Tailscale interface specifically —
    # never 0.0.0.0 on a machine with a public interface. Default here is
    # loopback-only, the safe default until a deployer consciously points it
    # at a real Tailscale IP.
    bind_host: str = os.environ.get("ETERNAL_BIND_HOST", "127.0.0.1")
    bind_port: int = int(os.environ.get("ETERNAL_BIND_PORT", 8422))

    # Cloud-provider try order. "harder calls" in the build plan is
    # ambiguous between (a) route to cloud only when local is unreachable,
    # or (b) route to cloud for queries judged more complex even when local
    # is healthy. Phase 2 implements (a) as the default, documented decision
    # (see README "Design decisions"); a caller can still force cloud via
    # force_cloud on a per-request basis for the (b) use case without Phase 2
    # having to guess a complexity heuristic nobody asked for yet.
    cloud_provider_order: List[str] = field(default_factory=lambda: ["anthropic", "openai"])

    def __post_init__(self):
        os.makedirs(self.data_dir, exist_ok=True)
        self.chroma_persist_dir = self.chroma_persist_dir or os.path.join(self.data_dir, "chroma")
        self.ledger_db_path = self.ledger_db_path or os.path.join(self.data_dir, "eternal_ledger.db")
