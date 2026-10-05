"""Composition root: builds every long-lived object exactly once, in one place.

Swapping a fake for a real implementation (or vice versa) is a config change
here - never an `if settings.environment == ...` sprinkled through the code.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from support_agent.agent.graph import build_graph
from support_agent.agent.tools import build_tools
from support_agent.core.config import CheckpointerBackend, RetrieverBackend, Settings
from support_agent.core.logging import get_logger
from support_agent.integrations.orders import HttpOrderService, InMemoryOrderService, OrderService
from support_agent.integrations.ticketing import (
    HttpTicketingClient,
    InMemoryTicketingClient,
    TicketingClient,
)
from support_agent.llm.factory import build_dependencies, build_embeddings
from support_agent.persistence.db import create_pool, ping
from support_agent.persistence.migrations import run_migrations
from support_agent.rag.retriever import InMemoryRetriever, PgVectorRetriever, Retriever
from support_agent.services.agent_service import AgentService

logger = get_logger(__name__)
HealthCheck = Callable[[], Awaitable[None]]
DEFAULT_KB_PATH = Path("data/knowledge_base")


@dataclass
class Runtime:
    agent_service: AgentService
    health_checks: dict[str, HealthCheck] = field(default_factory=dict)


RuntimeFactory = Callable[[Settings, AsyncExitStack], Awaitable[Runtime]]


def _http_client(stack: AsyncExitStack, base_url: str, timeout: float) -> httpx.AsyncClient:
    client = httpx.AsyncClient(base_url=base_url, timeout=timeout)
    stack.push_async_callback(client.aclose)
    return client


def build_integrations(
    settings: Settings, stack: AsyncExitStack
) -> tuple[OrderService, TicketingClient]:
    orders: OrderService = (
        HttpOrderService(
            _http_client(stack, settings.order_service_base_url, settings.http_timeout_seconds)
        )
        if settings.order_service_base_url
        else InMemoryOrderService()
    )
    ticketing: TicketingClient = (
        HttpTicketingClient(
            _http_client(stack, settings.ticketing_base_url, settings.http_timeout_seconds)
        )
        if settings.ticketing_base_url
        else InMemoryTicketingClient()
    )
    if settings.is_production and not (
        settings.order_service_base_url and settings.ticketing_base_url
    ):
        raise RuntimeError("In-memory integrations are not allowed in production.")
    return orders, ticketing


def _memory_retriever() -> Retriever:
    from support_agent.rag.ingest import load_chunks

    chunks = load_chunks(DEFAULT_KB_PATH) if DEFAULT_KB_PATH.exists() else []
    logger.warning("retriever.in_memory", chunks=len(chunks))
    return InMemoryRetriever(chunks)


async def build_runtime(settings: Settings, stack: AsyncExitStack) -> Runtime:
    health_checks: dict[str, HealthCheck] = {}
    pool: AsyncConnectionPool[Any] | None = None

    needs_db = (
        settings.checkpointer_backend == CheckpointerBackend.POSTGRES
        or settings.retriever_backend == RetrieverBackend.PGVECTOR
    )
    if needs_db:
        db_pool = await create_pool(settings)
        stack.push_async_callback(db_pool.close)
        pool = db_pool
        health_checks["postgres"] = lambda: ping(db_pool)
        if settings.run_migrations_on_startup:
            await run_migrations(db_pool)

    checkpointer: BaseCheckpointSaver[Any]
    if settings.checkpointer_backend == CheckpointerBackend.POSTGRES:
        assert pool is not None
        postgres_saver = AsyncPostgresSaver(pool)
        await postgres_saver.setup()  # idempotent; creates/migrates checkpoint tables
        checkpointer = postgres_saver
    else:
        checkpointer = InMemorySaver()

    retriever: Retriever
    if settings.retriever_backend == RetrieverBackend.PGVECTOR:
        assert pool is not None
        retriever = PgVectorRetriever(
            pool, build_embeddings(settings), settings.retrieval_candidates
        )
    else:
        retriever = _memory_retriever()

    orders, ticketing = build_integrations(settings, stack)
    deps = build_dependencies(
        settings, tools=build_tools(orders), retriever=retriever, ticketing=ticketing
    )
    graph = build_graph(deps, checkpointer)
    logger.info(
        "runtime.ready",
        checkpointer=settings.checkpointer_backend,
        retriever=settings.retriever_backend,
    )
    return Runtime(agent_service=AgentService(graph, settings), health_checks=health_checks)
