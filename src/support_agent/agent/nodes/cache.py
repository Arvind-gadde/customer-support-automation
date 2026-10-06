"""Semantic cache nodes: look up before RAG, store after a fully vetted answer.

The cache sits AFTER intent routing, so only FAQ questions ever reach it, and
the store node runs only after verify_answer AND guard_output both passed.
Cache failures never fail a turn: a broken cache just means a slower answer.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from support_agent.agent.dependencies import AgentDependencies
from support_agent.agent.state import AgentState
from support_agent.core.logging import get_logger
from support_agent.core.telemetry import set_span_attributes, traced_node

logger = get_logger(__name__)


def make_check_cache(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    enabled = deps.settings.semantic_cache_enabled

    @traced_node("check_cache")
    async def check_cache(state: AgentState) -> dict[str, Any]:
        humans = [m for m in state["messages"] if isinstance(m, HumanMessage)]
        # Follow-ups ("and for electronics?") depend on context: never cached.
        if not enabled or len(humans) != 1:
            return {"cacheable": False}
        try:
            hit = await deps.cache.lookup(humans[0].text)
        except Exception:
            logger.exception("cache.lookup_failed")
            return {"cacheable": True}
        set_span_attributes(**{"cache.hit": hit is not None})
        if hit is None:
            logger.info("cache.miss")
            return {"cacheable": True}
        logger.info("cache.hit", similarity=round(hit.similarity, 4))
        return {
            "cacheable": True,
            "cache_hit": True,
            "messages": [
                AIMessage(
                    content=hit.answer,
                    response_metadata={"source": "semantic_cache", "similarity": hit.similarity},
                )
            ],
        }

    return check_cache


def make_store_cache(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    @traced_node("store_cache")
    async def store_cache(state: AgentState) -> dict[str, Any]:
        question = next(m.text for m in state["messages"] if isinstance(m, HumanMessage))
        answer = state["messages"][-1].text
        sources = sorted({d["source"] for d in state.get("documents", [])})
        try:
            await deps.cache.store(question, answer, sources)
            logger.info("cache.stored", sources=sources)
        except Exception:
            logger.exception("cache.store_failed")
        return {}

    return store_cache
