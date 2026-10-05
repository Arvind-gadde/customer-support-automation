"""Graph state.

State is checkpointed after every node, so it must stay small and
JSON-serializable. Per-turn scratch fields are reset at the start of each
turn by `new_turn()` - otherwise turn N+1 would see turn N's documents.
"""

from __future__ import annotations

import operator
from enum import StrEnum
from typing import Annotated, Any, NotRequired, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph.message import add_messages


class Intent(StrEnum):
    FAQ = "faq"  # policy / how-to questions answered from the knowledge base
    ACCOUNT = "account"  # needs customer data or actions (orders, refunds)
    HUMAN = "human"  # customer explicitly wants a person, or is upset
    SMALLTALK = "smalltalk"  # greetings, thanks


class HandoffReason(StrEnum):
    CUSTOMER_REQUEST = "customer_request"
    LOW_CONFIDENCE = "low_confidence"
    NO_RELEVANT_KNOWLEDGE = "no_relevant_knowledge"
    UNGROUNDED_ANSWER = "ungrounded_answer"
    TOOL_ITERATION_LIMIT = "tool_iteration_limit"
    CONTENT_FILTERED = "content_filtered"


class RetrievedDoc(TypedDict):
    id: str
    source: str
    title: str
    content: str
    score: float


class AgentState(TypedDict):
    # Durable, conversation-level
    messages: Annotated[list[AnyMessage], add_messages]
    customer_id: str
    audit_log: NotRequired[Annotated[list[dict[str, Any]], operator.add]]

    # Per-turn scratch.
    # Enums are stored as their plain string VALUES (`Intent.FAQ.value`), never
    # as Enum objects: checkpoints must not contain custom Python types (see
    # tests/integration/test_checkpoint_safety.py). StrEnum == str, so
    # comparisons like `state["intent"] == Intent.FAQ` still work.
    intent: NotRequired[str | None]
    query: NotRequired[str]
    documents: NotRequired[list[RetrievedDoc]]
    draft_answer: NotRequired[str | None]
    rewrite_count: NotRequired[int]
    tool_iterations: NotRequired[int]
    guardrail_violation: NotRequired[str | None]
    handoff_reason: NotRequired[str | None]
    ticket_id: NotRequired[str | None]


def new_turn(message: str, customer_id: str) -> dict[str, Any]:
    """Graph input for a new user turn: the message plus a reset of scratch fields."""
    return {
        "messages": [HumanMessage(content=message)],
        "customer_id": customer_id,
        "intent": None,
        "query": message,
        "documents": [],
        "draft_answer": None,
        "rewrite_count": 0,
        "tool_iterations": 0,
        "guardrail_violation": None,
        "handoff_reason": None,
        "ticket_id": None,
    }
