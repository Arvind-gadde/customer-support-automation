"""Real Postgres + pgvector: migrations, hybrid search, durable checkpoints.

Skipped unless TEST_DATABASE_URL is set. CI runs it against a pgvector service
container (see .github/workflows/ci.yml); locally: `make up` then `make test-db`.
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import SecretStr

from support_agent.agent.graph import build_graph
from support_agent.persistence.db import create_pool
from support_agent.persistence.migrations import run_migrations
from support_agent.rag.ingest import ingest, load_chunks
from support_agent.rag.retriever import PgVectorRetriever
from support_agent.services.agent_service import AgentService
from support_agent.services.schemas import (
    ApprovalDecision,
    ChatRequest,
    ResumeRequest,
    TurnStatus,
)
from tests.conftest import KB
from tests.fakes import HashEmbeddings, make_deps

DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
async def pool(settings):  # type: ignore[no-untyped-def]
    settings.database_url = SecretStr(DB_URL or "")
    pool = await create_pool(settings)
    async with pool.connection() as conn:
        await conn.execute("DROP TABLE IF EXISTS kb_chunks, schema_migrations CASCADE")
    yield pool
    await pool.close()


async def test_migrations_are_idempotent(pool) -> None:
    assert await run_migrations(pool) == ["001_knowledge_base.sql"]
    assert await run_migrations(pool) == []


async def test_ingest_is_incremental_and_hybrid_search_finds_exact_terms(pool) -> None:
    await run_migrations(pool)
    embeddings = HashEmbeddings()
    chunks = load_chunks(KB)

    first = await ingest(pool, embeddings, chunks)
    second = await ingest(pool, embeddings, chunks)
    assert first["embedded"] == len(chunks)
    assert second["embedded"] == 0  # unchanged content is never re-embedded

    # Fake embeddings are random, so this proves the LEXICAL half of RRF works.
    results = await PgVectorRetriever(pool, embeddings).search("COD handling fee", top_k=3)
    assert results[0]["source"] == "payments.md"
    assert results[0]["title"] == "Cash on delivery"


async def test_interrupted_thread_survives_a_process_restart(settings, pool, retriever) -> None:
    """Approval requested on 'replica A', approved on 'replica B'."""
    saver = AsyncPostgresSaver(pool)
    await saver.setup()

    replica_a = AgentService(build_graph(make_deps(settings, retriever=retriever), saver), settings)
    pending = await replica_a.chat(
        ChatRequest(customer_id="cust_123", message="Refund ORD-1001 2499 rs please")
    )
    assert pending.status == TurnStatus.AWAITING_APPROVAL

    async with AsyncExitStack():
        replica_b = AgentService(
            build_graph(make_deps(settings, retriever=retriever), AsyncPostgresSaver(pool)),
            settings,
        )
        done = await replica_b.resume(
            pending.thread_id,
            ResumeRequest(decision=ApprovalDecision(approved=True, reviewer="priya")),
        )
    assert done.status == TurnStatus.COMPLETED
