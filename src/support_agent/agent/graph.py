"""Graph assembly. Read this file first: it IS the architecture.

Branches (rendered diagram: docs/architecture.md):
  guard_input      -> blocked_reply                     (prompt injection / oversized input)
  classify_intent  -> faq       : retrieve -> grade -> [rewrite -> retrieve] -> generate -> verify
                   -> account   : account_agent <-> review_tool_calls (HITL) <-> tools
                   -> smalltalk : smalltalk_reply
                   -> human     : create_ticket -> await_human (HITL)
Any branch that cannot answer safely falls through to create_ticket.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from support_agent.agent import edges
from support_agent.agent.dependencies import AgentDependencies
from support_agent.agent.nodes.account import make_account_agent, make_review_tool_calls
from support_agent.agent.nodes.guard import blocked_reply, make_guard_input
from support_agent.agent.nodes.handoff import await_human, make_create_ticket
from support_agent.agent.nodes.rag import (
    make_generate_answer,
    make_grade_documents,
    make_retrieve,
    make_rewrite_query,
    make_smalltalk_reply,
    make_verify_answer,
)
from support_agent.agent.nodes.routing import make_classify_intent
from support_agent.agent.state import AgentState

SupportGraph = CompiledStateGraph[AgentState, Any, Any, Any]


def build_graph(deps: AgentDependencies, checkpointer: BaseCheckpointSaver[Any]) -> SupportGraph:
    g = StateGraph(AgentState)

    g.add_node("guard_input", make_guard_input(deps))
    g.add_node("blocked_reply", blocked_reply)
    g.add_node("classify_intent", make_classify_intent(deps))
    # RAG branch
    g.add_node("retrieve", make_retrieve(deps))
    g.add_node("grade_documents", make_grade_documents(deps))
    g.add_node("rewrite_query", make_rewrite_query(deps))
    g.add_node("generate_answer", make_generate_answer(deps))
    g.add_node("verify_answer", make_verify_answer(deps))
    g.add_node("smalltalk_reply", make_smalltalk_reply(deps))
    # Tool-calling branch
    g.add_node("account_agent", make_account_agent(deps))
    g.add_node(
        "review_tool_calls",
        make_review_tool_calls(deps),
        destinations=("tools", "account_agent"),
    )
    g.add_node("tools", ToolNode(deps.tools, handle_tool_errors=True))
    # Handoff branch
    g.add_node("create_ticket", make_create_ticket(deps))
    g.add_node("await_human", await_human)

    g.add_edge(START, "guard_input")
    g.add_conditional_edges("guard_input", edges.after_guard)
    g.add_edge("blocked_reply", END)
    g.add_conditional_edges("classify_intent", edges.after_classify)

    g.add_edge("retrieve", "grade_documents")
    g.add_conditional_edges("grade_documents", edges.after_grade)
    g.add_edge("rewrite_query", "retrieve")
    g.add_edge("generate_answer", "verify_answer")
    g.add_conditional_edges("verify_answer", edges.after_verify)
    g.add_edge("smalltalk_reply", END)

    g.add_conditional_edges("account_agent", edges.after_account_agent)
    g.add_edge("tools", "account_agent")

    g.add_edge("create_ticket", "await_human")
    g.add_edge("await_human", END)

    return g.compile(checkpointer=checkpointer, name="support-agent")
