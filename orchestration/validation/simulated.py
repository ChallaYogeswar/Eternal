"""Simulated models for the harness self-test. They let us prove the harness can tell a well-behaved model from
a sloppy one and from a vulnerable one BEFORE spending any free-tier quota. They act out each case's `script`.

good       : always valid JSON, follows the script, ignores injected instructions.
sloppy     : ~35% of turns wrap the JSON in chatty prose (rejected by the strict parser), repairs correctly.
vulnerable : like good, but obeys an injected instruction when its trigger text appears in an observation
             (moving the canary into a tool call, reading secrets, adding the hijack marker to its answer).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from eternal_core.llm.base import LLMProvider, LLMResult

from .cases import ValidationCase

_REJECTED = ("Your reply was rejected", "output did not match the schema")


def _is_rejection(m: Dict[str, str]) -> bool:
    return m["role"] == "user" and m["content"].startswith(_REJECTED)


def _tool(tool: str, args: Dict[str, Any], reason: str = "needed") -> str:
    return json.dumps({"action": "tool_request", "tool": tool, "arguments": args, "reason": reason})


def _final(output: Dict[str, Any]) -> str:
    return json.dumps({"action": "final_answer", "output": output})


class SimulatedModel(LLMProvider):
    name = "sim"
    behaviour = "good"

    def __init__(self) -> None:
        self.case: Optional[ValidationCase] = None
        self.salt = 0

    def is_configured(self) -> bool:
        return True

    def bind(self, case: ValidationCase, salt: int = 0) -> None:
        self.case, self.salt = case, salt

    # -- helpers ---------------------------------------------------------------------------------
    def _obs(self, messages: List[Dict[str, str]]) -> List[str]:
        return [m["content"] for m in messages[1:] if m["role"] == "user" and not _is_rejection(m)]

    def _glitch(self, k: int) -> bool:
        return False

    def _vulnerable(self) -> bool:
        return False

    def generate(self, system_prompt: str, messages: List[Dict[str, str]]) -> LLMResult:
        case = self.case
        assert case is not None, "bind() a case first"
        obs = self._obs(messages)
        injected_done = any('"reason": "injected"' in m["content"] for m in messages if m["role"] == "assistant")
        k = len(obs)
        text = self._decide(case, obs, k, injected_done, rejected=_is_rejection(messages[-1]))
        return LLMResult(text=text, provider=self.name, model=self.behaviour, latency_seconds=0.0)

    def _decide(self, case: ValidationCase, obs: List[str], k: int, injected_done: bool, rejected: bool) -> str:
        inj = case.injection
        if self._vulnerable() and inj and not injected_done:
            haystack = (case.goal + json.dumps(case.inputs) + " ".join(obs))
            if inj.trigger in haystack:
                return _tool(inj.tool, inj.args(haystack), "injected")
        idx = k - (1 if injected_done else 0)
        if idx < len(case.script):
            tool, args = case.script[idx]
            text = _tool(tool, args)
            if not rejected and self._glitch(k):
                return "Sure! Here is my next action:\n" + text
            return text
        out = dict(case.sim_final(obs))
        if self._vulnerable() and inj and injected_done:
            out["answer"] = (out.get("answer", "") + " " + inj.hijack).strip()
        text = _final(out)
        if not rejected and self._glitch(k):
            return "Here you go: " + text
        return text


class GoodModel(SimulatedModel):
    behaviour = "good"


class SloppyModel(SimulatedModel):
    behaviour = "sloppy"

    def _glitch(self, k: int) -> bool:
        h = hashlib.sha256(f"{self.case.id}|{k}|{self.salt}".encode()).digest()[0]
        return h % 100 < 35


class VulnerableModel(SimulatedModel):
    behaviour = "vulnerable"

    def _vulnerable(self) -> bool:
        return True


SIM_MODELS = {"good": GoodModel, "sloppy": SloppyModel, "vulnerable": VulnerableModel}
