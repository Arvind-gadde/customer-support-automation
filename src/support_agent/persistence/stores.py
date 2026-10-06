"""Write-side stores for analytics data: LLM usage and customer feedback.

Both are append/upsert-only and never on the critical path: a failed write is
logged, never surfaced to the customer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from psycopg_pool import AsyncConnectionPool

from support_agent.llm.usage import ModelUsage


@dataclass(frozen=True, slots=True)
class UsageRecord:
    thread_id: str
    request_id: str | None
    model: str
    usage: ModelUsage
    cost_usd: Decimal | None


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    thread_id: str
    message_id: str
    customer_id: str
    rating: int  # +1 / -1
    comment: str | None
    prompt_version: str


class UsageStore(Protocol):
    async def record(self, records: list[UsageRecord]) -> None: ...


class FeedbackStore(Protocol):
    async def upsert(self, record: FeedbackRecord) -> None: ...


@dataclass
class InMemoryUsageStore:
    records: list[UsageRecord] = field(default_factory=list)

    async def record(self, records: list[UsageRecord]) -> None:
        self.records.extend(records)


@dataclass
class InMemoryFeedbackStore:
    records: dict[tuple[str, str], FeedbackRecord] = field(default_factory=dict)

    async def upsert(self, record: FeedbackRecord) -> None:
        self.records[(record.thread_id, record.message_id)] = record


class PgUsageStore:
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def record(self, records: list[UsageRecord]) -> None:
        if not records:
            return
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.executemany(
                """INSERT INTO llm_usage (thread_id, request_id, model, calls, input_tokens,
                       cached_input_tokens, output_tokens, cost_usd)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                [
                    (
                        r.thread_id,
                        r.request_id,
                        r.model,
                        r.usage.calls,
                        r.usage.input_tokens,
                        r.usage.cached_input_tokens,
                        r.usage.output_tokens,
                        r.cost_usd or 0,
                    )
                    for r in records
                ],
            )


class PgFeedbackStore:
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def upsert(self, record: FeedbackRecord) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """INSERT INTO feedback (thread_id, message_id, customer_id, rating, comment,
                       prompt_version)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (thread_id, message_id) DO UPDATE SET
                       rating = EXCLUDED.rating, comment = EXCLUDED.comment,
                       prompt_version = EXCLUDED.prompt_version, updated_at = now()""",
                (
                    record.thread_id,
                    record.message_id,
                    record.customer_id,
                    record.rating,
                    record.comment,
                    record.prompt_version,
                ),
            )
