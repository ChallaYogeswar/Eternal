"""
Multi-provider LLM routing with fallback — Eternal_Master_Build_Plan.md
Section 5.1 baseline feature, verbatim.

Design decision (documented, not hidden): the build plan's phrase "cloud
fallback for harder calls" is ambiguous between (a) route to cloud only
when the local model is unreachable/fails, or (b) route to cloud whenever a
query is judged "harder" even if local is healthy. Phase 2 implements (a) —
plain availability fallback — because it's the literal reading of "fallback"
as a term of art, it's what Section 5.1 actually names the feature
("routing with fallback", not "routing by difficulty"), and it needs no
complexity heuristic invented from nothing. `force_cloud` exists as an
explicit escape hatch for interpretation (b) without Phase 2 having to guess
one.
"""
from dataclasses import dataclass
from typing import List, Dict, Optional

from .base import LLMProvider, LLMResult, LLMError


@dataclass
class RouterAttempt:
    provider: str
    ok: bool
    error: Optional[str] = None


class AllProvidersFailedError(Exception):
    def __init__(self, attempts: List[RouterAttempt]):
        self.attempts = attempts
        summary = "; ".join(f"{a.provider}: {a.error}" for a in attempts)
        super().__init__(f"every provider in the chain failed — {summary}")


class LLMRouter:
    """Tries providers in the order given; returns the first success.
    Every attempt (including skipped-because-not-configured ones) is
    recorded so the caller can log exactly what happened, per the build
    plan's audit-trail expectations."""

    def __init__(self, providers: List[LLMProvider]):
        self.providers = providers

    def generate(self, system_prompt: str, messages: List[Dict[str, str]]) -> "RoutedResult":
        attempts: List[RouterAttempt] = []
        for provider in self.providers:
            if not provider.is_configured():
                attempts.append(RouterAttempt(provider.name, ok=False, error="not configured"))
                continue
            try:
                result = provider.generate(system_prompt, messages)
                attempts.append(RouterAttempt(provider.name, ok=True))
                return RoutedResult(result=result, attempts=attempts)
            except LLMError as e:
                attempts.append(RouterAttempt(provider.name, ok=False, error=str(e)))
                continue
        raise AllProvidersFailedError(attempts)


@dataclass
class RoutedResult:
    result: LLMResult
    attempts: List[RouterAttempt]
