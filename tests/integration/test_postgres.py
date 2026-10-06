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
        await conn.execute(
            "DROP TABLE IF EXISTS kb_chunks, semantic_cache, llm_usage, feedback, "
            "schema_migrations CASCADE"
        )
    yield pool
    await pool.close()


async def test_migrations_are_idempotent(pool) -> None:
    assert await run_migrations(pool) == [
        "001_knowledge_base.sql",
        "002_semantic_cache.sql",
        "003_usage_and_feedback.sql",
    ]
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


async def test_pg_semantic_cache_threshold_namespace_and_ingest_wipe(pool) -> None:
    from support_agent.rag.semantic_cache import PgSemanticCache

    await run_migrations(pool)
    embeddings = HashEmbeddings()

    def cache(namespace: str = "v1:gpt-4.1-mini", ttl: int = 24) -> PgSemanticCache:
        return PgSemanticCache(pool, embeddings, namespace=namespace, threshold=0.95, ttl_hours=ttl)

    await cache().store("What is the return window?", "10 days.", ["returns.md"])
    hit = await cache().lookup("what is the RETURN window??")  # same after normalising
    assert hit is not None
    assert hit.answer == "10 days."
    assert hit.similarity > 0.99
    assert await cache().lookup("Do you ship to Canada?") is None  # below threshold
    assert await cache("v2:gpt-4.1-mini").lookup("What is the return window?") is None

    async with pool.connection() as conn:  # expire it
        await conn.execute("UPDATE semantic_cache SET created_at = now() - interval '2 days'")
    assert await cache().lookup("What is the return window?") is None

    await cache().store("What is the return window?", "10 days.", ["returns.md"])
    await ingest(pool, embeddings, load_chunks(KB))  # KB changed => cache wiped
    assert await cache().lookup("What is the return window?") is None


async def test_usage_and_feedback_are_persisted(pool) -> None:
    from decimal import Decimal

    from support_agent.llm.usage import ModelUsage
    from support_agent.persistence.stores import (
        FeedbackRecord,
        PgFeedbackStore,
        PgUsageStore,
        UsageRecord,
    )

    await run_migrations(pool)
    await PgUsageStore(pool).record(
        [UsageRecord("t1", "req1", "gpt-4.1-mini", ModelUsage(3, 900, 100, 150), Decimal("0.0006"))]
    )
    feedback = PgFeedbackStore(pool)
    vote = FeedbackRecord("t1", "m1", "cust_123", 1, None, "v1")
    await feedback.upsert(vote)
    await feedback.upsert(FeedbackRecord("t1", "m1", "cust_123", -1, "wrong", "v1"))

    async with pool.connection() as conn:
        usage = await (await conn.execute("SELECT * FROM llm_usage")).fetchone()
        votes = await (await conn.execute("SELECT rating, comment FROM feedback")).fetchall()
    assert usage["cost_usd"] == Decimal("0.000600")
    assert votes == [{"rating": -1, "comment": "wrong"}]
