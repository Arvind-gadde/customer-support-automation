"""Postgres connection pool, shared by the LangGraph checkpointer and the retriever."""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from support_agent.core.config import Settings


async def create_pool(settings: Settings) -> AsyncConnectionPool[Any]:
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        conninfo=settings.database_url.get_secret_value(),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        # The LangGraph checkpointer REQUIRES autocommit + dict_row.
        # prepare_threshold=0 keeps us compatible with PgBouncer (transaction mode).
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        # Detect connections killed by Azure's idle timeout before handing them out.
        check=AsyncConnectionPool.check_connection,
        open=False,
    )
    await pool.open(wait=True, timeout=30)
    return pool


async def ping(pool: AsyncConnectionPool[Any]) -> None:
    async with pool.connection() as conn:
        await conn.execute("SELECT 1")
