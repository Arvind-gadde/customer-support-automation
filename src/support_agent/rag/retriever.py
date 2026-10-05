"""Retrievers.

PgVectorRetriever runs HYBRID search: dense (pgvector cosine) + lexical
(Postgres full-text), fused with Reciprocal Rank Fusion. Dense search misses
exact tokens like "ORD-1001" or "COD"; lexical misses paraphrases. RRF gets the
best of both without having to calibrate two incompatible score scales.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Protocol

from langchain_core.embeddings import Embeddings
from psycopg_pool import AsyncConnectionPool

from support_agent.agent.state import RetrievedDoc
from support_agent.rag.chunking import Chunk

RRF_K = 60  # standard constant from the original RRF paper

_HYBRID_SQL = """
WITH semantic AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> %(embedding)s::vector) AS rank
    FROM kb_chunks
    ORDER BY embedding <=> %(embedding)s::vector
    LIMIT %(candidates)s
),
lexical AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(search_tsv, q) DESC) AS rank
    FROM kb_chunks, websearch_to_tsquery('english', %(query)s) AS q
    WHERE search_tsv @@ q
    ORDER BY ts_rank_cd(search_tsv, q) DESC
    LIMIT %(candidates)s
)
SELECT c.id, c.source, c.title, c.content,
       COALESCE(1.0 / (%(rrf_k)s + s.rank), 0)
     + COALESCE(1.0 / (%(rrf_k)s + l.rank), 0) AS score
FROM semantic s
FULL OUTER JOIN lexical l ON s.id = l.id
JOIN kb_chunks c ON c.id = COALESCE(s.id, l.id)
ORDER BY score DESC
LIMIT %(top_k)s
"""


class Retriever(Protocol):
    async def search(self, query: str, top_k: int) -> list[RetrievedDoc]: ...


def to_pgvector(embedding: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in embedding) + "]"


class PgVectorRetriever:
    def __init__(
        self, pool: AsyncConnectionPool[Any], embeddings: Embeddings, candidates: int = 20
    ) -> None:
        self._pool = pool
        self._embeddings = embeddings
        self._candidates = candidates

    async def search(self, query: str, top_k: int) -> list[RetrievedDoc]:
        embedding = await self._embeddings.aembed_query(query)
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                _HYBRID_SQL,
                {
                    "embedding": to_pgvector(embedding),
                    "query": query,
                    "candidates": self._candidates,
                    "rrf_k": RRF_K,
                    "top_k": top_k,
                },
            )
            rows = await cur.fetchall()
        return [
            RetrievedDoc(
                id=row["id"],
                source=row["source"],
                title=row["title"],
                content=row["content"],
                score=float(row["score"]),
            )
            for row in rows
        ]


_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "can",
        "do",
        "does",
        "for",
        "how",
        "i",
        "in",
        "is",
        "it",
        "my",
        "of",
        "on",
        "or",
        "the",
        "to",
        "what",
        "when",
        "where",
        "why",
        "with",
        "you",
    ]
)


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS}


class InMemoryRetriever:
    """Keyword-overlap retriever for tests and offline local dev. Not for prod."""

    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self._chunks = list(chunks)

    async def search(self, query: str, top_k: int) -> list[RetrievedDoc]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        scored: list[tuple[float, Chunk]] = []
        for chunk in self._chunks:
            overlap = len(query_tokens & _tokens(f"{chunk.title} {chunk.content}"))
            if overlap:
                scored.append((overlap / len(query_tokens), chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            RetrievedDoc(id=c.id, source=c.source, title=c.title, content=c.content, score=s)
            for s, c in scored[:top_k]
        ]
