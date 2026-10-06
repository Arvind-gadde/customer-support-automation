"""Conditional edges. Pure functions of state => unit-tested in isolation."""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import AIMessage

from support_agent.agent.state import AgentState, Intent


def after_guard(state: AgentState) -> Literal["blocked_reply", "classify_intent"]:
    return "blocked_reply" if state.get("guardrail_violation") else "classify_intent"


def after_classify(
    state: AgentState,
) -> Literal["check_cache", "account_agent", "smalltalk_reply", "create_ticket"]:
    match state.get("intent"):
        case Intent.FAQ:
            return "check_cache"
        case Intent.ACCOUNT:
            return "account_agent"
        case Intent.SMALLTALK:
            return "smalltalk_reply"
        case _:
            return "create_ticket"


def after_cache(state: AgentState) -> Literal["guard_output", "retrieve"]:
    return "guard_output" if state.get("cache_hit") else "retrieve"


def after_grade(state: AgentState) -> Literal["generate_answer", "rewrite_query", "create_ticket"]:
    if state.get("documents"):
        return "generate_answer"
    if state.get("handoff_reason"):
        return "create_ticket"
    return "rewrite_query"


def after_verify(state: AgentState) -> Literal["create_ticket", "guard_output"]:
    return "create_ticket" if state.get("handoff_reason") else "guard_output"


def after_account_agent(
    state: AgentState,
) -> Literal["review_tool_calls", "create_ticket", "guard_output"]:
    if state.get("handoff_reason"):
        return "create_ticket"
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "review_tool_calls"
    return "guard_output"


def after_guard_output(state: AgentState) -> Literal["create_ticket", "store_cache", "__end__"]:
    if state.get("handoff_reason"):
        return "create_ticket"
    if (
        state.get("intent") == Intent.FAQ
        and state.get("cacheable")
        and not state.get("cache_hit")
        and not state.get("output_modified")
        and state.get("documents")
    ):
        return "store_cache"
    return "__end__"  # == END; the literal keeps the Literal[] return type honest
