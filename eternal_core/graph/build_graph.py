"""
Assembles the compiled LangGraph state machine:

    retrieve -> try_local --[response?]--> finish -> END
                    |no
                    v
                try_cloud --[response?]--> finish -> END
                    |no
                    v
             retrieval_fallback -> finish -> END

This is the "Core Orchestrator — LangGraph + Ollama, cloud fallback" box
from Eternal_Master_Build_Plan.md Section 4's architecture diagram, scoped
to exactly what Phase 2 is allowed to touch: RAG retrieval and text
generation, nothing else. No node here has access to any tool, tracker, or
filesystem-write capability — see README, "Zero write access, by
construction" for the full argument, and tests/test_zero_write_access.py
for the automated check.
"""
from typing import List

from langgraph.graph import StateGraph, END

from ..llm.base import LLMProvider
from ..rag.retriever import Retriever
from .state import EternalState
from .nodes import (
    make_retrieve_node,
    make_try_local_node,
    route_after_local,
    make_try_cloud_node,
    route_after_cloud,
    make_tools_node,
    route_after_retrieve,
    retrieval_fallback_node,
    make_finish_node,
)


def build_graph(
    retriever: Retriever,
    ollama_provider: LLMProvider,
    cloud_providers: List[LLMProvider],
    ledger,
    top_k: int = 5,
    tool_gateway=None,
):
    graph = StateGraph(EternalState)

    graph.add_node("retrieve", make_retrieve_node(retriever, top_k))
    if tool_gateway is not None:
        graph.add_node("tools", make_tools_node(tool_gateway))
    graph.add_node("try_local", make_try_local_node(ollama_provider))
    graph.add_node("try_cloud", make_try_cloud_node(cloud_providers))
    graph.add_node("retrieval_fallback", retrieval_fallback_node)
    graph.add_node("finish", make_finish_node(ledger))

    graph.set_entry_point("retrieve")
    if tool_gateway is not None:
        graph.add_conditional_edges("retrieve", route_after_retrieve, {"tools": "tools", "llm": "try_local"})
        graph.add_edge("tools", "try_local")
    else:
        graph.add_edge("retrieve", "try_local")
    graph.add_conditional_edges("try_local", route_after_local, {"finish": "finish", "cloud": "try_cloud"})
    graph.add_conditional_edges(
        "try_cloud", route_after_cloud, {"finish": "finish", "degraded": "retrieval_fallback"}
    )
    graph.add_edge("retrieval_fallback", "finish")
    graph.add_edge("finish", END)

    return graph.compile()
