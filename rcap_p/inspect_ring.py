"""
Ring 2 — INSPECT.

Named inspect_ring.py, not inspect.py, deliberately: Python's stdlib
`inspect` module is imported all over the ecosystem (pydantic, FastMCP,
pytest itself), and a same-named local module would shadow it the moment
anything did `import inspect` after this package was on the path. Cheap
mistake to avoid, not worth the debugging time it would cost later.

RCAP whitepaper Section 3.1 (Ring 2): "Behavioral and signature-based
anomaly detection; tags sessions for containment versus pass-through."
Eternal_Master_Build_Plan.md Section 6.2 calls this "the important one":
generalized to gate *every* MCP tool response, not just fetched web pages
— "a compromised CinemaVault/study-tracker/NSE-data server can return
injection-shaped output too." Two methods, both implemented here:

  1. Structured-data path: strict schema/type validation via the tool's
     declared Pydantic output_schema (registry.py). "Cheaper and stronger"
     per the build plan — a field that doesn't match its declared type is
     an unambiguous signal, no heuristics needed.
  2. Free-text path: heuristic pattern-scanning for injection-shaped
     language, applied to raw string responses AND recursively to every
     string leaf inside structured data — schema-valid JSON can still carry
     an injected instruction inside a perfectly well-typed `title` or
     `notes` string. Skipping this because "the schema passed" would be
     exactly the kind of narrow reading the build plan calls out as the
     original design's flaw (Untitled_document.md: "Every fetched
     page/email/PDF is scanned... *before* it enters the model's context").

Flags, never silently strips (Section 6.2's own words) — a MALICIOUS or
SUSPICIOUS verdict is returned as data, not swallowed; contain.py decides
what happens to the content, this module only decides what it *is*.
"""
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, Type

from pydantic import BaseModel, ValidationError

from .config import Config
from .models import InspectFinding, InspectVerdict

# --- Heuristic pattern set -------------------------------------------------
# Grouped by the manipulation pattern they target, not written as an
# exhaustive adversarial-ML classifier — this is a real, working, but
# deliberately non-exhaustive first line of defense. See README, "Known
# Phase-3 simplifications" for what this genuinely does and doesn't catch.
# Patterns are lowercase; scanning lowercases input first.

_INSTRUCTION_OVERRIDE_PATTERNS = [
    r"ignore (all|any|the)?\s*(previous|prior|above|preceding)\s*instructions?",
    r"disregard (all|any|the)?\s*(previous|prior|above|preceding)\s*(instructions?|prompt)",
    r"forget (all|any|the)?\s*(previous|prior|above)\s*(instructions?|context|rules)",
    r"new instructions?:\s",
    r"system\s*(prompt|override|instruction)s?\s*[:\-]",
    r"you are now\s+\w",
    r"act as (an?|the)\s",
    r"admin\s*mode\s*(enabled|activated|on)",
    r"do anything now",
    r"jailbreak",
    r"bypass (all|any|the)?\s*(security|safety|rcap|inspect|qap|containment)",
    r"disable (all|any|the)?\s*(security|safety|rcap|inspect|qap|containment)",
]

_EXFILTRATION_PATTERNS = [
    r"send (this|the|all)\s+(data|contents?|information|credentials?|vault|secrets?)\s+to",
    r"email\s+(this|the|all)\s+.*\s+to\s+[\w.\-]+@",
    r"post\s+(this|the)\s+(data|contents?|payload)\s+to\s+https?://",
    r"exfiltrat",
    r"reveal (your|the)\s+(system prompt|instructions?|api key|credentials?|vault|token)",
    r"print (your|the)\s+(system prompt|instructions?|api key|credentials?)",
    r"what is your (system prompt|api key|secret)",
]

_TOOL_STEERING_PATTERNS = [
    r"call the tool\s+[\w_]+\s+with",
    r"invoke\s+[\w_]+\(",
    r"use the (admin|root|owner)\s+(account|credential|token)",
    r"grant (full|admin|root)\s+access",
]

_BLOB_PATTERN = r"[A-Za-z0-9+/]{80,}={0,2}"

_OBFUSCATION_PATTERNS = [
    _BLOB_PATTERN,                      # long base64-looking blob
    r"[\u200b\u200c\u200d\ufeff]",     # zero-width chars, a classic steganographic-injection trick
    r"<!--.*?(ignore|system|instruction|override).*?-->",  # directive hidden in an HTML comment
]

_ALL_PATTERN_GROUPS = {
    "instruction_override": _INSTRUCTION_OVERRIDE_PATTERNS,
    "exfiltration": _EXFILTRATION_PATTERNS,
    "tool_steering": _TOOL_STEERING_PATTERNS,
    "obfuscation": _OBFUSCATION_PATTERNS,
}

_COMPILED_GROUPS = {
    group: [re.compile(p, re.IGNORECASE | re.DOTALL) for p in patterns]
    for group, patterns in _ALL_PATTERN_GROUPS.items()
}


def _normalize_for_scan(text: str) -> str:
    """Normalize common Unicode and split-token obfuscation before scanning."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", normalized)
    # Keep the original spacing for ordinary patterns and add a compact view
    # so "i g n o r e" and punctuation-split directives are also inspected.
    compact = re.sub(r"[^\w]+", "", normalized, flags=re.UNICODE)
    return f"{normalized}\n{compact}"


def scan_text(text: str) -> Tuple[List[str], List[str]]:
    """Runs every pattern group against `text`. Returns
    (matched_pattern_labels, reasons) — not yet a verdict; verdict depends
    on config thresholds, computed by the caller (InspectRing.inspect_text)
    so the raw scan stays independently testable."""
    if not text:
        return [], []
    matched: List[str] = []
    reasons: List[str] = []
    normalized = _normalize_for_scan(text)
    # The "compact" view (all punctuation/whitespace stripped) exists to catch split-token
    # directives like "i g n o r e". It must NOT be fed to the long-blob/base64 heuristic:
    # stripping spaces turns ANY ordinary prose sentence of 80+ characters into one long
    # alphanumeric run, which false-positived every tool response with a long note field
    # (found running the real MCP e2e: trading_check_recovery_math was held for approval).
    spaced_only = normalized.split("\n", 1)[0]
    for group, compiled in _COMPILED_GROUPS.items():
        for pattern in compiled:
            haystack = spaced_only if pattern.pattern == _BLOB_PATTERN else normalized
            if pattern.search(haystack):
                label = f"{group}:{pattern.pattern[:40]}"
                matched.append(label)
                reasons.append(f"matched {group} pattern")
    return matched, reasons


def _extract_string_leaves(data: Any, _depth: int = 0) -> List[str]:
    """Recursively pulls every string value out of arbitrarily nested
    dict/list structures. This is what lets INSPECT catch an injected
    instruction hiding inside a schema-valid field (e.g. a movie `title`)
    rather than only scanning top-level free text."""
    if _depth > 12:  # guard against pathological/adversarial nesting depth
        return []
    leaves: List[str] = []
    if isinstance(data, str):
        leaves.append(data)
    elif isinstance(data, dict):
        for v in data.values():
            leaves.extend(_extract_string_leaves(v, _depth + 1))
    elif isinstance(data, (list, tuple)):
        for v in data:
            leaves.extend(_extract_string_leaves(v, _depth + 1))
    return leaves


class InspectRing:
    def __init__(self, config: Config):
        self.config = config

    def _verdict_from_match_count(self, count: int) -> InspectVerdict:
        if count >= self.config.inspect_malicious_threshold:
            return InspectVerdict.MALICIOUS
        if count >= self.config.inspect_suspicious_threshold:
            return InspectVerdict.SUSPICIOUS
        return InspectVerdict.CLEAN

    def inspect_text(self, text: str) -> InspectFinding:
        matched, reasons = scan_text(text)
        verdict = self._verdict_from_match_count(len(matched))
        return InspectFinding(verdict=verdict, reasons=reasons, matched_patterns=matched)

    def inspect_structured(self, data: dict, schema: Optional[Type[BaseModel]]) -> InspectFinding:
        reasons: List[str] = []
        matched: List[str] = []
        schema_broke = False

        if schema is not None:
            try:
                schema.model_validate(data)
            except ValidationError as e:
                schema_broke = True
                reasons.append(f"structured response failed schema validation: {e.error_count()} error(s)")

        # Defense in depth: scan every string leaf even if the schema passed
        # — see module docstring on why "the schema passed" isn't sufficient
        # on its own.
        for leaf in _extract_string_leaves(data):
            leaf_matched, leaf_reasons = scan_text(leaf)
            matched.extend(leaf_matched)
            reasons.extend(leaf_reasons)

        verdict = self._verdict_from_match_count(len(matched))
        if schema_broke and verdict == InspectVerdict.CLEAN:
            # A broken schema is a real signal on its own, even with zero
            # pattern matches — promote at least to SUSPICIOUS rather than
            # reporting CLEAN just because no *language* pattern fired.
            verdict = InspectVerdict.SUSPICIOUS

        return InspectFinding(verdict=verdict, reasons=reasons, matched_patterns=matched)

    def inspect(self, content: Any, schema: Optional[Type[BaseModel]] = None) -> InspectFinding:
        """Single entry point the gateway calls for any tool response,
        structured or not — this is the "gates every MCP tool response, not
        just fetched web pages" fix, applied uniformly regardless of which
        tool produced the content."""
        if isinstance(content, str):
            return self.inspect_text(content)
        if isinstance(content, (dict, list)):
            # Normalize to a dict-shaped container for the schema check;
            # lists of records are scanned leaf-wise regardless.
            data = content if isinstance(content, dict) else {"items": content}
            return self.inspect_structured(data, schema)
        # Fallback: stringify and text-scan. Reaching this branch means a
        # tool returned something INSPECT wasn't designed around (e.g. a
        # raw number) — safe to treat as clean but worth being explicit
        # about rather than silently no-op'ing.
        return self.inspect_text(str(content))
