"""Second-stage reranking.

Hybrid search (vector + full-text + RRF) is good at RECALL: the right chunk is
usually somewhere in the top 20. A cross-encoder reranker reads the query and
each chunk TOGETHER, so it is much better at PRECISION: putting the right chunk
first. Retrieve wide, rerank narrow.

Why Cohere and not an OpenAI call? Reranking is a dedicated cross-encoder
model: one cheap, fast call scoring 20 chunks. OpenAI has no rerank endpoint,
and an LLM-as-reranker duplicates what the grader node already does, at
several times the latency. The reranker is optional (RERANKER_BACKEND=none).
"""

from __future__ import annotations

from typing import Protocol

import httpx

from support_agent.agent.state import RetrievedDoc
from support_agent.core.errors import UpstreamError
from support_agent.core.logging import get_logger
from support_agent.integrations.http import request_json

logger = get_logger(__name__)


class Reranker(Protocol):
    async def rerank(
        self, query: str, docs: list[RetrievedDoc], top_n: int
    ) -> list[RetrievedDoc]: ...


class PassthroughReranker:
    """Keeps retrieval order. Used when no reranker is configured."""

    async def rerank(self, query: str, docs: list[RetrievedDoc], top_n: int) -> list[RetrievedDoc]:
        return docs[:top_n]


class CohereReranker:
    def __init__(self, client: httpx.AsyncClient, model: str) -> None:
        # client carries base_url=https://api.cohere.com and the auth header.
        self._client = client
        self._model = model

    async def rerank(self, query: str, docs: list[RetrievedDoc], top_n: int) -> list[RetrievedDoc]:
        if len(docs) <= 1:
            return docs[:top_n]
        try:
            response = await request_json(
                self._client,
                "POST",
                "/v2/rerank",
                service="cohere-rerank",
                json={
                    "model": self._model,
                    "query": query,
                    "documents": [f"{d['title']}\n{d['content']}" for d in docs],
                    "top_n": top_n,
                },
            )
            results = response.json()["results"]
        except (UpstreamError, KeyError, ValueError):
            # A reranker outage must not take answers down: degrade to RRF order.
            logger.warning("rerank.degraded_to_retrieval_order")
            return docs[:top_n]
        ranked: list[RetrievedDoc] = []
        for r in results:
            if 0 <= r["index"] < len(docs):
                doc = docs[r["index"]]
                ranked.append(
                    RetrievedDoc(
                        id=doc["id"],
                        source=doc["source"],
                        title=doc["title"],
                        content=doc["content"],
                        score=float(r["relevance_score"]),
                    )
                )
        return ranked
