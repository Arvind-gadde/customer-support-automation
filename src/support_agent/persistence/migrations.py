"""Minimal forward-only SQL migration runner.

Why not Alembic? We have no ORM models to autogenerate from, and the
checkpointer owns its own tables. Plain numbered .sql files are easier to
review. An advisory lock makes it safe when N replicas start at once.
"""

from __future__ import annotations

from importlib import resources
from typing import Any

from psycopg_pool import AsyncConnectionPool

from support_agent.core.logging import get_logger

logger = get_logger(__name__)
_LOCK_KEY = 482_917_301  # arbitrary, constant app-wide


async def run_migrations(pool: AsyncConnectionPool[Any]) -> list[str]:
    files = sorted(
        (
            f
            for f in resources.files("support_agent.persistence.sql").iterdir()
            if f.name.endswith(".sql")
        ),
        key=lambda f: f.name,
    )
    applied_now: list[str] = []
    async with pool.connection() as conn:
        await conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        try:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version TEXT PRIMARY KEY,
                       applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
            )
            cur = await conn.execute("SELECT version FROM schema_migrations")
            applied = {row["version"] for row in await cur.fetchall()}
            for file in files:
                if file.name in applied:
                    continue
                async with conn.transaction():
                    # prepare=False: migration files hold multiple statements, which
                    # cannot be server-side prepared (the pool sets prepare_threshold=0).
                    await conn.execute(file.read_text(encoding="utf-8"), prepare=False)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s)", (file.name,)
                    )
                applied_now.append(file.name)
                logger.info("migration.applied", version=file.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
    return applied_now
