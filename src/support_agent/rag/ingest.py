"""Knowledge-base ingestion: markdown -> chunks -> embeddings -> pgvector.

Idempotent by design: unchanged chunks (same content hash) are not re-embedded,
and chunks that disappeared from a source file are deleted. Safe to re-run from
CI or a Container Apps Job on every docs change.

    python -m support_agent.rag.ingest --path data/knowledge_base
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from langchain_core.embeddings import Embeddings
from psycopg_pool import AsyncConnectionPool

from support_agent.core.config import get_settings
from support_agent.core.logging import configure_logging, get_logger
from support_agent.llm.factory import build_embeddings
from support_agent.persistence.db import create_pool
from support_agent.persistence.migrations import run_migrations
from support_agent.rag.chunking import Chunk, chunk_markdown
from support_agent.rag.retriever import to_pgvector

logger = get_logger(__name__)
EMBED_BATCH_SIZE = 64


def load_chunks(root: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(root.rglob("*.md")):
        source = path.relative_to(root).as_posix()
        chunks.extend(chunk_markdown(path.read_text(encoding="utf-8"), source=source))
    return chunks


async def ingest(
    pool: AsyncConnectionPool[Any], embeddings: Embeddings, chunks: list[Chunk]
) -> dict[str, int]:
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT id, content_hash FROM kb_chunks")
        existing = {row["id"]: row["content_hash"] for row in await cur.fetchall()}

    changed = [c for c in chunks if existing.get(c.id) != c.content_hash]
    stale_ids = set(existing) - {c.id for c in chunks}

    for start in range(0, len(changed), EMBED_BATCH_SIZE):
        batch = changed[start : start + EMBED_BATCH_SIZE]
        vectors = await embeddings.aembed_documents([f"{c.title}\n{c.content}" for c in batch])
        async with pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            await cur.executemany(
                """
                INSERT INTO kb_chunks
                    (id, source, title, chunk_index, content, content_hash, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                ON CONFLICT (id) DO UPDATE SET
                    source = EXCLUDED.source, title = EXCLUDED.title,
                    chunk_index = EXCLUDED.chunk_index, content = EXCLUDED.content,
                    content_hash = EXCLUDED.content_hash, embedding = EXCLUDED.embedding,
                    updated_at = now()
                """,
                [
                    (
                        c.id,
                        c.source,
                        c.title,
                        c.chunk_index,
                        c.content,
                        c.content_hash,
                        to_pgvector(v),
                    )
                    for c, v in zip(batch, vectors, strict=True)
                ],
            )
        logger.info("ingest.batch_upserted", size=len(batch))

    if stale_ids:
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("DELETE FROM kb_chunks WHERE id = ANY(%s)", (list(stale_ids),))

    stats = {"total": len(chunks), "embedded": len(changed), "deleted": len(stale_ids)}
    logger.info("ingest.completed", **stats)
    return stats


async def _run(path: Path) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    pool = await create_pool(settings)
    try:
        await run_migrations(pool)
        await ingest(pool, build_embeddings(settings), load_chunks(path))
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest markdown knowledge base into pgvector.")
    parser.add_argument("--path", type=Path, default=Path("data/knowledge_base"))
    args = parser.parse_args()
    asyncio.run(_run(args.path))


if __name__ == "__main__":
    main()
