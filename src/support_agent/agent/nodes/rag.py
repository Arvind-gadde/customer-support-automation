"""Corrective RAG (CRAG) pipeline.

retrieve -> grade -> (rewrite -> retrieve)* -> generate -> verify

1. Grading filters retrieved chunks with an LLM judge, so irrelevant context
   never reaches the answer prompt (the #1 cause of confident wrong answers).
2. If nothing is relevant we rewrite the query ONCE and retry, then give up
   and escalate to a human rather than answer from thin air.
3. The draft is checked for groundedness before it is shown to the customer.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from support_agent.agent.dependencies import AgentDependencies
from support_agent.agent.messages import dialogue, last_human_text, transcript
from support_agent.agent.prompts import (
    ANSWER_SYSTEM,
    GRADER_SYSTEM,
    GROUNDEDNESS_SYSTEM,
    REWRITE_SYSTEM,
    SMALLTALK_SYSTEM,
)
from support_agent.agent.state import AgentState, HandoffReason, RetrievedDoc
from support_agent.core.logging import get_logger
from support_agent.core.telemetry import set_span_attributes, traced_node
from support_agent.llm.errors import is_content_filter_error

logger = get_logger(__name__)


def format_docs(docs: list[RetrievedDoc]) -> str:
    return "\n\n".join(
        f'<doc id="{d["id"]}" source="{d["source"]}" title="{d["title"]}">\n{d["content"]}\n</doc>'
        for d in docs
    )


def make_retrieve(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    @traced_node("retrieve")
    async def retrieve(state: AgentState) -> dict[str, Any]:
        query = state.get("query") or last_human_text(state["messages"])
        try:
            docs = await deps.retriever.search(query, deps.settings.retrieval_top_k)
        except Exception:
            # Degrade, don't crash: an empty result routes to rewrite/handoff.
            logger.exception("retrieval.failed")
            docs = []
        set_span_attributes(**{"rag.retrieved": len(docs)})
        return {"documents": docs}

    return retrieve


def make_grade_documents(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    max_rewrites = deps.settings.max_query_rewrites

    @traced_node("grade_documents")
    async def grade_documents(state: AgentState) -> dict[str, Any]:
        docs = state.get("documents", [])
        relevant: list[RetrievedDoc] = []
        if docs:
            question = last_human_text(state["messages"])
            grade = await deps.grader.ainvoke(
                [
                    SystemMessage(GRADER_SYSTEM),
                    HumanMessage(f"Question: {question}\n\nDocuments:\n{format_docs(docs)}"),
                ]
            )
            keep = set(grade.relevant_doc_ids)
            relevant = [d for d in docs if d["id"] in keep]

        set_span_attributes(**{"rag.relevant": len(relevant)})
        update: dict[str, Any] = {"documents": relevant}
        if not relevant and state.get("rewrite_count", 0) >= max_rewrites:
            update["handoff_reason"] = HandoffReason.NO_RELEVANT_KNOWLEDGE.value
        return update

    return grade_documents


def make_rewrite_query(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    @traced_node("rewrite_query")
    async def rewrite_query(state: AgentState) -> dict[str, Any]:
        convo = transcript(state["messages"], deps.settings.history_window_messages)
        rewritten = await deps.rewriter.ainvoke(
            [SystemMessage(REWRITE_SYSTEM), HumanMessage(f"Conversation:\n{convo}")]
        )
        logger.info("rag.query_rewritten", attempt=state.get("rewrite_count", 0) + 1)
        return {"query": rewritten.query, "rewrite_count": state.get("rewrite_count", 0) + 1}

    return rewrite_query


def make_generate_answer(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    @traced_node("generate_answer")
    async def generate_answer(state: AgentState) -> dict[str, Any]:
        system = ANSWER_SYSTEM.format(context=format_docs(state.get("documents", [])))
        history = dialogue(state["messages"], deps.settings.history_window_messages)
        try:
            reply = await deps.answerer.ainvoke([SystemMessage(system), *history])
        except Exception as exc:
            if not is_content_filter_error(exc):
                raise
            return {"handoff_reason": HandoffReason.CONTENT_FILTERED.value, "draft_answer": None}
        return {"draft_answer": reply.text}

    return generate_answer


def make_verify_answer(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    enabled = deps.settings.enable_groundedness_check

    @traced_node("verify_answer")
    async def verify_answer(state: AgentState) -> dict[str, Any]:
        draft = state.get("draft_answer")
        if not draft:  # generation was content-filtered; handoff_reason already set
            return {}
        if enabled:
            verdict = await deps.groundedness.ainvoke(
                [
                    SystemMessage(GROUNDEDNESS_SYSTEM),
                    HumanMessage(
                        f"Context:\n{format_docs(state.get('documents', []))}\n\nAnswer:\n{draft}"
                    ),
                ]
            )
            if not verdict.grounded:
                logger.warning("rag.ungrounded_answer", claims=verdict.unsupported_claims)
                return {
                    "handoff_reason": HandoffReason.UNGROUNDED_ANSWER.value,
                    "draft_answer": None,
                }
        return {"messages": [AIMessage(content=draft)], "draft_answer": None}

    return verify_answer


def make_smalltalk_reply(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    @traced_node("smalltalk_reply")
    async def smalltalk_reply(state: AgentState) -> dict[str, Any]:
        history = dialogue(state["messages"], 4)
        reply = await deps.answerer.ainvoke([SystemMessage(SMALLTALK_SYSTEM), *history])
        return {"messages": [AIMessage(content=reply.text)]}

    return smalltalk_reply
