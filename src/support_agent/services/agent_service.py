"""Application service: the only place that talks to the compiled graph.

Owns: thread ownership checks, per-thread concurrency, interrupt <-> API status
mapping, and resume validation. Routes stay thin; the graph stays HTTP-agnostic.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from uuid import uuid4

import openai
import structlog
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command, StateSnapshot

from support_agent.agent.graph import SupportGraph
from support_agent.agent.prompts import PROMPT_VERSION
from support_agent.agent.state import new_turn
from support_agent.core.config import Settings
from support_agent.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
)
from support_agent.core.logging import get_logger
from support_agent.core.telemetry import set_span_attributes
from support_agent.llm.usage import UsageTracker, cost_usd
from support_agent.persistence.stores import (
    FeedbackRecord,
    FeedbackStore,
    InMemoryFeedbackStore,
    InMemoryUsageStore,
    UsageRecord,
    UsageStore,
)
from support_agent.services.schemas import (
    ChatRequest,
    FeedbackRequest,
    FeedbackResult,
    MessageView,
    PendingAction,
    ResumeRequest,
    ThreadView,
    TurnResult,
    TurnStatus,
)

logger = get_logger(__name__)

# Nodes whose LLM tokens are customer-facing (internal judges/routers are not streamed).
STREAMABLE_NODES = frozenset({"generate_answer", "smalltalk_reply", "account_agent"})
_STATUS_BY_INTERRUPT = {
    "approval": TurnStatus.AWAITING_APPROVAL,
    "handoff": TurnStatus.AWAITING_HUMAN,
}


class AgentService:
    def __init__(
        self,
        graph: SupportGraph,
        settings: Settings,
        *,
        usage_store: UsageStore | None = None,
        feedback_store: FeedbackStore | None = None,
    ) -> None:
        self._graph = graph
        self._settings = settings
        self._usage_store = usage_store or InMemoryUsageStore()
        self._feedback_store = feedback_store or InMemoryFeedbackStore()
        # Serialises turns per thread within this replica. Across replicas, a
        # client must not send concurrent turns for one thread (documented in API).
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ------------------------------------------------------------------ public

    async def chat(self, request: ChatRequest) -> TurnResult:
        thread_id = request.thread_id or str(uuid4())
        config = self._config(thread_id)
        async with self._locks[thread_id]:
            await self._assert_can_send(config, request.customer_id)
            await self._invoke(new_turn(request.message, request.customer_id), config)
            return await self._result(thread_id, config)

    async def open_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """Validate, then return a Server-Sent Events stream.

        Validation happens HERE, before the HTTP response starts - once a
        StreamingResponse has sent its 200 headers, an exception can no longer
        become a proper 403/409.

        Events: `start`, `token`* (customer-facing nodes only), then `final`.
        Clients MUST treat `final.reply` as authoritative: a streamed draft can
        still be rejected by the groundedness check and replaced by a handoff.
        """
        thread_id = request.thread_id or str(uuid4())
        config = self._config(thread_id)
        await self._assert_can_send(config, request.customer_id)
        return self._stream(request, thread_id, config)

    async def _stream(
        self, request: ChatRequest, thread_id: str, config: RunnableConfig
    ) -> AsyncIterator[str]:
        async with self._locks[thread_id]:
            yield _sse("start", {"thread_id": thread_id})
            tracker = UsageTracker()
            try:
                async for chunk, metadata in self._graph.astream(
                    new_turn(request.message, request.customer_id),
                    {**config, "callbacks": [tracker]},
                    stream_mode="messages",
                ):
                    if (
                        isinstance(chunk, AIMessageChunk)
                        and chunk.text
                        and isinstance(metadata, dict)
                        and metadata.get("langgraph_node") in STREAMABLE_NODES
                    ):
                        yield _sse("token", {"text": chunk.text})
                result = await self._result(thread_id, config)
            except Exception:
                logger.exception("stream.failed")
                yield _sse("error", {"code": "internal_error", "message": "Something went wrong."})
                return
            finally:
                await self._record_usage(thread_id, tracker)
            yield _sse("final", result.model_dump(mode="json"))

    async def resume(self, thread_id: str, request: ResumeRequest) -> TurnResult:
        config = self._config(thread_id)
        async with self._locks[thread_id]:
            snapshot = await self._snapshot_or_404(config)
            pending = _pending_interrupt(snapshot)
            if pending is None:
                raise ConflictError("This thread is not waiting for a decision.")
            if pending.type != request.decision.type:
                raise ConflictError(
                    f"Thread is waiting for '{pending.type}', got '{request.decision.type}'."
                )
            structlog.contextvars.bind_contextvars(thread_id=thread_id)
            logger.info("thread.resumed", kind=pending.type)
            await self._invoke(Command(resume=request.decision.model_dump()), config)
            return await self._result(thread_id, config)

    async def get_thread(self, thread_id: str, customer_id: str | None) -> ThreadView:
        config = self._config(thread_id)
        snapshot = await self._snapshot_or_404(config)
        owner = snapshot.values["customer_id"]
        if customer_id is not None and owner != customer_id:
            raise NotFoundError("Thread not found.")  # 404, not 403: don't leak existence
        pending = _pending_interrupt(snapshot)
        return ThreadView(
            thread_id=thread_id,
            customer_id=owner,
            status=_status(pending),
            messages=_message_views(snapshot.values.get("messages", [])),
            pending=pending,
        )

    async def feedback(self, thread_id: str, request: FeedbackRequest) -> FeedbackResult:
        """Thumbs up/down on one assistant message. Re-voting overwrites."""
        snapshot = await self._snapshot_or_404(self._config(thread_id))
        if snapshot.values["customer_id"] != request.customer_id:
            raise NotFoundError("Thread not found.")
        rated = next(
            (
                m
                for m in snapshot.values.get("messages", [])
                if isinstance(m, AIMessage) and not m.tool_calls and m.id == request.message_id
            ),
            None,
        )
        if rated is None:
            raise NotFoundError("Message not found in this thread.")
        await self._feedback_store.upsert(
            FeedbackRecord(
                thread_id=thread_id,
                message_id=request.message_id,
                customer_id=request.customer_id,
                rating=1 if request.rating == "up" else -1,
                comment=request.comment,
                prompt_version=PROMPT_VERSION,
            )
        )
        # Online quality signal: dashboards/alerts key on this event.
        logger.info(
            "feedback.received",
            rating=request.rating,
            source=rated.response_metadata.get("source", "llm"),
            prompt_version=PROMPT_VERSION,
        )
        return FeedbackResult(
            thread_id=thread_id, message_id=request.message_id, rating=request.rating
        )

    # ----------------------------------------------------------------- helpers

    async def _record_usage(self, thread_id: str, tracker: UsageTracker) -> None:
        if not tracker.by_model:
            return
        pricing = self._settings.model_pricing
        request_id = structlog.contextvars.get_contextvars().get("request_id")
        records = [
            UsageRecord(thread_id, request_id, model, usage, cost_usd(model, usage, pricing))
            for model, usage in tracker.by_model.items()
        ]
        total_cost = sum((r.cost_usd for r in records if r.cost_usd is not None), Decimal(0))
        unpriced = [r.model for r in records if r.cost_usd is None]
        set_span_attributes(
            **{"llm.total_tokens": tracker.total_tokens, "llm.cost_usd": float(total_cost)}
        )
        logger.info(
            "llm.usage",
            calls=sum(r.usage.calls for r in records),
            total_tokens=tracker.total_tokens,
            cost_usd=str(total_cost),
            unpriced_models=unpriced or None,
        )
        try:
            await self._usage_store.record(records)
        except Exception:
            logger.exception("usage.record_failed")  # analytics never fail a turn

    async def _invoke(self, graph_input: Any, config: RunnableConfig) -> None:
        tracker = UsageTracker()
        thread_id = config["configurable"]["thread_id"]
        try:
            await self._graph.ainvoke(graph_input, {**config, "callbacks": [tracker]})
        except (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
        ) as exc:
            # Retries + fallback model are already exhausted at this point.
            # The checkpoint is intact, so the client can simply retry the turn.
            logger.error("llm.unavailable", error_type=type(exc).__name__)
            raise ServiceUnavailableError(
                "The assistant is temporarily busy. Please try again shortly."
            ) from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            # Revoked/rotated key or a model the project can't use. Not the
            # customer's fault and not retryable by them, but for them it's an outage.
            logger.critical("llm.misconfigured", error_type=type(exc).__name__)
            raise ServiceUnavailableError(
                "The assistant is temporarily unavailable. Please try again later."
            ) from exc
        finally:
            await self._record_usage(thread_id, tracker)

    def _config(self, thread_id: str) -> RunnableConfig:
        structlog.contextvars.bind_contextvars(thread_id=thread_id)
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self._settings.graph_recursion_limit,
        }

    async def _assert_can_send(self, config: RunnableConfig, customer_id: str) -> None:
        snapshot = await self._graph.aget_state(config)
        if not snapshot.values:
            return  # brand-new thread
        if snapshot.values.get("customer_id") != customer_id:
            raise ForbiddenError("This conversation belongs to another customer.")
        if _pending_interrupt(snapshot) is not None:
            raise ConflictError("This conversation is waiting on our support team.")

    async def _snapshot_or_404(self, config: RunnableConfig) -> StateSnapshot:
        snapshot = await self._graph.aget_state(config)
        if not snapshot.values:
            raise NotFoundError("Thread not found.")
        return snapshot

    async def _result(self, thread_id: str, config: RunnableConfig) -> TurnResult:
        snapshot = await self._graph.aget_state(config)
        values = snapshot.values
        pending = _pending_interrupt(snapshot)
        last_ai = None if pending else _last_ai_message(values.get("messages", []))
        reply = (
            pending.payload.get("customer_message")
            if pending
            else (last_ai.text if last_ai else None)
        )
        intent = values.get("intent")
        return TurnResult(
            thread_id=thread_id,
            status=_status(pending),
            reply=reply,
            reply_message_id=last_ai.id if last_ai else None,
            cached=bool(values.get("cache_hit")),
            intent=str(intent) if intent else None,
            ticket_id=values.get("ticket_id"),
            pending=pending,
        )


def _pending_interrupt(snapshot: StateSnapshot) -> PendingAction | None:
    interrupts = [i for task in snapshot.tasks for i in task.interrupts]
    if not interrupts:
        return None
    payload: dict[str, Any] = dict(interrupts[0].value)
    return PendingAction(type=payload["type"], payload=payload)


def _status(pending: PendingAction | None) -> TurnStatus:
    return TurnStatus.COMPLETED if pending is None else _STATUS_BY_INTERRUPT[pending.type]


def _last_ai_message(messages: list[Any]) -> AIMessage | None:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls and message.text:
            return message
        if isinstance(message, HumanMessage):
            break
    return None


def _message_views(messages: list[Any]) -> list[MessageView]:
    views: list[MessageView] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            views.append(MessageView(message_id=m.id, role="customer", content=m.text))
        elif isinstance(m, AIMessage) and not m.tool_calls and m.text:
            role = "human_agent" if m.name == "human_agent" else "assistant"
            views.append(MessageView(message_id=m.id, role=role, content=m.text))
    return views


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
