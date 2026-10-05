"""Deterministic stand-ins for every LLM call.

Because nodes depend on Runnables (not AzureChatOpenAI), a fake is just a
RunnableLambda. No mocking/patching, no network, millisecond-fast tests.
"""

from __future__ import annotations

import hashlib
import random
import re
from collections.abc import Callable
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from support_agent.agent.dependencies import AgentDependencies
from support_agent.agent.schemas import (
    GroundednessVerdict,
    IntentDecision,
    RelevanceGrade,
    RewrittenQuery,
)
from support_agent.agent.state import Intent
from support_agent.agent.tools import build_tools
from support_agent.core.config import Settings
from support_agent.integrations.orders import InMemoryOrderService
from support_agent.integrations.ticketing import InMemoryTicketingClient
from support_agent.rag.retriever import Retriever

Fn = Callable[[list[BaseMessage]], Any]
_ORDER = re.compile(r"ORD-\d+", re.IGNORECASE)
_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:rs|inr|rupees)", re.IGNORECASE)


def keyword_router(messages: list[BaseMessage]) -> IntentDecision:
    text = messages[-1].text.lower().rsplit("customer:", 1)[-1]
    if "human" in text or "agent" in text:
        return IntentDecision(intent=Intent.HUMAN, confidence=0.95, rationale="asked")
    if "order" in text or "refund ord" in text or "ord-" in text:
        return IntentDecision(intent=Intent.ACCOUNT, confidence=0.95, rationale="order")
    if text.strip() in {"hi", "hello", "thanks"}:
        return IntentDecision(intent=Intent.SMALLTALK, confidence=0.9, rationale="greeting")
    if "maybe" in text:
        return IntentDecision(intent=Intent.FAQ, confidence=0.3, rationale="unsure")
    return IntentDecision(intent=Intent.FAQ, confidence=0.9, rationale="policy")


def grade_all(messages: list[BaseMessage]) -> RelevanceGrade:
    return RelevanceGrade(relevant_doc_ids=re.findall(r'<doc id="([^"]+)"', messages[-1].text))


def scripted_account_agent(messages: list[BaseMessage]) -> AIMessage:
    """Plays the LLM: look up / refund based on the latest human message."""
    last = messages[-1]
    if isinstance(last, ToolMessage):
        return AIMessage(content=f"Update on your request: {last.text}")
    human = next(m for m in reversed(messages) if isinstance(m, HumanMessage)).text
    order = _ORDER.search(human)
    if order is None:
        return AIMessage(content="Which order do you mean?")
    amount = _AMOUNT.search(human)
    if "refund" in human.lower() and amount:
        call = {
            "name": "issue_refund",
            "args": {
                "order_id": order.group(),
                "amount": float(amount.group(1)),
                "reason": "damaged",
            },
        }
    else:
        call = {"name": "lookup_order", "args": {"order_id": order.group()}}
    return AIMessage(
        content="", tool_calls=[{**call, "id": f"call_{len(messages)}", "type": "tool_call"}]
    )


def make_deps(
    settings: Settings,
    *,
    retriever: Retriever,
    orders: InMemoryOrderService | None = None,
    ticketing: InMemoryTicketingClient | None = None,
    router: Fn = keyword_router,
    grader: Fn = grade_all,
    rewriter: Fn | None = None,
    groundedness: Fn | None = None,
    answerer: Fn | None = None,
    account_agent: Fn = scripted_account_agent,
) -> AgentDependencies:
    orders = orders or InMemoryOrderService()
    return AgentDependencies(
        settings=settings,
        router=RunnableLambda(router),
        grader=RunnableLambda(grader),
        rewriter=RunnableLambda(rewriter or (lambda m: RewrittenQuery(query="refund policy"))),
        groundedness=RunnableLambda(
            groundedness or (lambda m: GroundednessVerdict(grounded=True, unsupported_claims=[]))
        ),
        answerer=RunnableLambda(
            answerer or (lambda m: AIMessage(content="Returns are accepted within 10 days."))
        ),
        account_agent=RunnableLambda(account_agent),
        tools=build_tools(orders),
        retriever=retriever,
        ticketing=ticketing or InMemoryTicketingClient(),
    )


class HashEmbeddings(Embeddings):
    """Deterministic pseudo-embeddings (no numpy). Similarity is meaningless by design."""

    def __init__(self, size: int = 1536) -> None:
        self.size = size

    def _embed(self, text: str) -> list[float]:
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
        rng = random.Random(seed)  # noqa: S311 - not crypto
        return [rng.uniform(-1, 1) for _ in range(self.size)]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)
