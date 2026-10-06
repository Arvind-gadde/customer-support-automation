"""Semantic answer cache for FAQ questions.

"What's your return window?" and "how many days do I get to return stuff?"
should cost one LLM pipeline run, not two. We embed the question, find the
nearest cached question, and reuse its answer above a HIGH similarity bar.

Correctness rules (a wrong cache hit is a confidently wrong answer):
  - Only FAQ answers, never account answers: those contain one customer's data.
  - Only first-turn questions: "and for electronics?" means nothing without
    the conversation, so follow-ups are never looked up or stored.
  - Only answers that passed the groundedness check AND the output guard.
  - Namespaced by prompt version + model: changing either starts a fresh cache.
  - TTL-bound, and wiped by ingestion whenever the knowledge base changes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from langchain_core.embeddings import Embeddings
from psycopg_pool import AsyncConnectionPool

from support_agent.rag.retriever import to_pgvector


@dataclass(frozen=True, slots=True)
class CachedAnswer:
    answer: str
    sources: list[str]
    similarity: float


class SemanticCache(Protocol):
    async def lookup(self, question: str) -> CachedAnswer | None: ...
    async def store(self, question: str, answer: str, sources: list[str]) -> None: ...


def normalize(question: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", question.lower())).strip()


def cache_key(namespace: str, question: str) -> str:
    return hashlib.sha256(f"{namespace}\n{normalize(question)}".encode()).hexdigest()


class NullSemanticCache:
    async def lookup(self, question: str) -> CachedAnswer | None:
        return None

    async def store(self, question: str, answer: str, sources: list[str]) -> None:
        return None


@dataclass
class InMemorySemanticCache:
    """Exact match on the normalised question. For tests and local dev only."""

    namespace: str
    ttl: timedelta = timedelta(hours=24)
    _entries: dict[str, tuple[CachedAnswer, datetime]] = field(default_factory=dict)

    async def lookup(self, question: str) -> CachedAnswer | None:
        hit = self._entries.get(cache_key(self.namespace, question))
        if hit is None or datetime.now(UTC) - hit[1] > self.ttl:
            return None
        return hit[0]

    async def store(self, question: str, answer: str, sources: list[str]) -> None:
        self._entries[cache_key(self.namespace, question)] = (
            CachedAnswer(answer, sources, 1.0),
            datetime.now(UTC),
        )


_LOOKUP_SQL = """
SELECT id, answer, sources, 1 - (embedding <=> %(embedding)s::vector) AS similarity
FROM semantic_cache
WHERE namespace = %(namespace)s
  AND created_at > now() - make_interval(hours => %(ttl)s)
ORDER BY embedding <=> %(embedding)s::vector
LIMIT 1
"""

_STORE_SQL = """
INSERT INTO semantic_cache (id, namespace, question, answer, sources, embedding)
VALUES (%(id)s, %(namespace)s, %(question)s, %(answer)s, %(sources)s, %(embedding)s::vector)
ON CONFLICT (id) DO UPDATE SET
    answer = EXCLUDED.answer, sources = EXCLUDED.sources,
    embedding = EXCLUDED.embedding, created_at = now(), hits = 0
"""


class PgSemanticCache:
    def __init__(
        self,
        pool: AsyncConnectionPool[Any],
        embeddings: Embeddings,
        *,
        namespace: str,
        threshold: float,
        ttl_hours: int,
    ) -> None:
        self._pool = pool
        self._embeddings = embeddings
        self._namespace = namespace
        self._threshold = threshold
        self._ttl_hours = ttl_hours

    async def lookup(self, question: str) -> CachedAnswer | None:
        embedding = to_pgvector(await self._embeddings.aembed_query(normalize(question)))
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                _LOOKUP_SQL,
                {"embedding": embedding, "namespace": self._namespace, "ttl": self._ttl_hours},
            )
            row = await cur.fetchone()
            if row is None or float(row["similarity"]) < self._threshold:
                return None
            await cur.execute(
                "UPDATE semantic_cache SET hits = hits + 1 WHERE id = %s", (row["id"],)
            )
        return CachedAnswer(row["answer"], list(row["sources"]), float(row["similarity"]))

    async def store(self, question: str, answer: str, sources: list[str]) -> None:
        embedding = to_pgvector(await self._embeddings.aembed_query(normalize(question)))
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                _STORE_SQL,
                {
                    "id": cache_key(self._namespace, question),
                    "namespace": self._namespace,
                    "question": question,
                    "answer": answer,
                    "sources": sources,
                    "embedding": embedding,
                },
            )
            # Opportunistic cleanup; cheap thanks to the created_at index.
            await cur.execute(
                "DELETE FROM semantic_cache WHERE created_at < now() - make_interval(hours => %s)",
                (self._ttl_hours,),
            )


async def clear_semantic_cache(pool: AsyncConnectionPool[Any]) -> int:
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("DELETE FROM semantic_cache")
        return int(cur.rowcount)
