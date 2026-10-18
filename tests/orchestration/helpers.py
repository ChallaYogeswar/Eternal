import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel

from eternal_core.llm.base import LLMError, LLMProvider, LLMResult, LLMUnavailableError
from orchestration.broker import BrokerFactory
from orchestration.contracts import (AgentSpec, Budget, Finding, ModelPolicy, Provenance, Sensitivity, Task,
                                     ToolInfo, ToolTier, Severity, Labels)
from orchestration.ledger import RunLedger
from orchestration.model_policy import PolicyRouter
from orchestration.runtime import AgentRunner


class Out(BaseModel):
    answer: str


class FOut(BaseModel):
    findings: List[Finding]
    summary: str = ""


class ScriptedProvider(LLMProvider):
    """Deterministic fake model: pops one scripted reply per call (str, or an Exception to raise)."""
    def __init__(self, replies, name="scripted", configured=True, on_call: Optional[Callable[[int], None]] = None):
        self.replies, self.name, self._configured, self.on_call = list(replies), name, configured, on_call
        self.calls: List[Dict[str, Any]] = []

    def is_configured(self) -> bool:
        return self._configured

    def generate(self, system_prompt, messages):
        self.calls.append({"system": system_prompt, "messages": list(messages)})
        if self.on_call:
            self.on_call(len(self.calls))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return LLMResult(text=r, provider=self.name, model="fake", latency_seconds=0.0)


def tool_req(tool, arguments=None, reason="because"):
    return json.dumps({"action": "tool_request", "tool": tool, "arguments": arguments or {}, "reason": reason})


def final(output):
    return json.dumps({"action": "final_answer", "output": output})


class FakeGateway:
    """Records every call; `responses` maps tool -> value | callable(kwargs)."""
    def __init__(self, responses=None):
        self.calls: List[Dict[str, Any]] = []
        self.responses = responses or {}

    def call_tool(self, **kwargs):
        self.calls.append(kwargs)
        r = self.responses.get(kwargs["tool_name"], {"ok": True, "content": {"data": "x"}})
        return r(kwargs) if callable(r) else r


class DocArgs(BaseModel):
    path: str


def catalog() -> Dict[str, ToolInfo]:
    T, S, P = ToolTier, Sensitivity, Provenance
    items = [
        ToolInfo("read_doc", T.READ_ONLY, P.TOOL_OUTPUT, S.INTERNAL, args_model=DocArgs,
                 source_extractor=lambda a, c: [f"file:{a['path']}"]),
        ToolInfo("web_search", T.READ_ONLY, P.WEB, S.PUBLIC, source_extractor=lambda a, c: ["https://example.org/a"]),
        ToolInfo("rag_query", T.READ_ONLY, P.RAG_INTERNAL, S.INTERNAL),
        ToolInfo("pub_tool", T.READ_ONLY, P.TOOL_OUTPUT, S.PUBLIC),
        ToolInfo("secret_tool", T.READ_ONLY, P.TOOL_OUTPUT, S.SECRET),
        ToolInfo("save_draft", T.WRITE, P.TOOL_OUTPUT, S.INTERNAL),
        ToolInfo("check_ledger", T.READ_ONLY, P.TOOL_OUTPUT, S.INTERNAL, is_check=True, check_severity=Severity.HIGH),
        ToolInfo("nuke", T.SENSITIVE, P.TOOL_OUTPUT, S.SECRET),
    ]
    return {i.name: i for i in items}


def make_spec(allowed=("read_doc", "pub_tool"), *, can_write=False, tier=ToolTier.READ_ONLY,
              sens=Sensitivity.INTERNAL, policy=ModelPolicy.LOCAL_ONLY, budget=None, schema=Out, name="tester"):
    return AgentSpec(name=name, purpose="test", system_prompt="You are a test agent.", allowed_tools=frozenset(allowed),
                     max_tool_tier=tier, max_data_sensitivity=sens, output_schema=schema,
                     budget=budget or Budget(), model_policy=policy, can_write=can_write)


def build(tmp_path, spec, replies, *, gateway=None, cloud=None, alive=None, clock=None, consent=None,
          on_call=None, rotate=None, local_configured=True):
    local = ScriptedProvider(replies, "local", configured=local_configured, on_call=on_call)
    router = PolicyRouter([local], cloud or [])
    gw = gateway or FakeGateway()
    ledger = RunLedger(str(tmp_path / "run_ledger.db"))
    kw = {}
    if clock:
        kw["clock"] = clock
    runner = AgentRunner(spec, router, BrokerFactory(gw, catalog()), ledger, consent=consent,
                         rotate_token_provider=rotate, session_alive=alive, **kw)
    return runner, local, gw, ledger


def task(goal="do it", labels=None, run_id="r1", origin="typed"):
    return Task(task_id="t1", run_id=run_id, goal=goal, labels=labels or Labels(), origin_channel=origin)
