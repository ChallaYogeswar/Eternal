"""
LLM provider abstraction.

Eternal_Master_Build_Plan.md Section 5.1 (baseline): "Multi-provider LLM
routing with fallback." Section 3: "Ollama, 3B-class local model as the
default. Claude / GPT-4o as cloud fallback for harder calls."

Every provider below implements the same three-method contract so the
router (router.py) and the LangGraph nodes (graph/nodes.py) never need to
know which concrete backend they're talking to — same seam pattern as
qap_p/hardware_key.py's HardwareKeyProvider: one abstract contract, swap
implementations freely.

Deliberately NOT part of this contract: any notion of "tools" or "function
calling". generate() takes a system prompt and a message list and returns
text — nothing here can hand a provider a capability to call out to
anything else. That's what makes "zero write access to anything" (Phase 2
exit criteria) true by construction rather than by convention: there is no
code path in this whole phase through which an LLM response can trigger a
write, because no provider implementation is ever given anything to call.
"""
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict


class LLMError(Exception):
    """Base class for all provider failures."""


class LLMUnavailableError(LLMError):
    """The provider could not be reached at all (connection refused, DNS
    failure, timeout). This is the signal the router treats as 'try the
    next provider' rather than 'this backend is broken'."""


class LLMAuthError(LLMError):
    """Reached the provider, but authentication failed (missing/invalid API
    key). Distinguished from LLMUnavailableError because it means the
    provider *would* work if configured correctly — worth surfacing
    differently in health checks."""


class LLMResponseError(LLMError):
    """Reached the provider, authenticated fine, but got back something
    that couldn't be turned into a usable response (malformed payload,
    unexpected error status, empty completion)."""


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    latency_seconds: float


class LLMProvider(ABC):
    name: str = "unnamed"

    @abstractmethod
    def is_configured(self) -> bool:
        """Structural check only (e.g. 'is an API key present') — does NOT
        guarantee the provider is reachable right now. Reachability is only
        ever proven by generate() actually succeeding or raising
        LLMUnavailableError."""

    @abstractmethod
    def generate(self, system_prompt: str, messages: List[Dict[str, str]]) -> LLMResult:
        """messages is a list of {"role": "user"|"assistant", "content": str}
        in chronological order, NOT including the system prompt. Raises one
        of the LLMError subclasses above on any failure; never returns a
        partial/fabricated result."""


def _time_call(fn):
    """Small helper so every concrete provider reports real, measured
    latency rather than a guess — useful for the usage/health surface later
    and for tests that assert fallback actually happened quickly."""
    start = time.time()
    result = fn()
    return result, time.time() - start
