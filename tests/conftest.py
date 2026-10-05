from __future__ import annotations

import os

# Fail on any custom type in checkpoints. LangGraph will block deserialising
# unregistered types in a future release; in prod that would break every
# paused conversation on upgrade. State must hold plain JSON-ish values only.
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from support_agent.agent.graph import build_graph
from support_agent.core.config import (
    CheckpointerBackend,
    Environment,
    RetrieverBackend,
    Settings,
)
from support_agent.integrations.orders import InMemoryOrderService
from support_agent.integrations.ticketing import InMemoryTicketingClient
from support_agent.rag.ingest import load_chunks
from support_agent.rag.retriever import InMemoryRetriever
from support_agent.services.agent_service import AgentService
from tests.fakes import make_deps

KB = Path(__file__).resolve().parents[1] / "data" / "knowledge_base"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # never read a developer's .env in tests
        environment=Environment.TEST,
        log_json=False,
        client_api_keys="client-key",
        staff_api_keys="staff-key",
        checkpointer_backend=CheckpointerBackend.MEMORY,
        retriever_backend=RetrieverBackend.MEMORY,
        refund_auto_approve_limit=50.0,
    )


@pytest.fixture
def retriever() -> InMemoryRetriever:
    return InMemoryRetriever(load_chunks(KB))


@pytest.fixture
def orders() -> InMemoryOrderService:
    return InMemoryOrderService()


@pytest.fixture
def ticketing() -> InMemoryTicketingClient:
    return InMemoryTicketingClient()


@pytest.fixture
def service_factory(settings, retriever, orders, ticketing):  # type: ignore[no-untyped-def]
    """Build an AgentService, optionally overriding individual fake LLM calls."""

    def factory(**overrides):  # type: ignore[no-untyped-def]
        deps = make_deps(
            settings, retriever=retriever, orders=orders, ticketing=ticketing, **overrides
        )
        return AgentService(build_graph(deps, InMemorySaver()), settings)

    return factory
