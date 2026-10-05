# ADR 0002: Postgres for checkpoints *and* vectors

**Status:** accepted

## Context
We need durable conversation state (LangGraph checkpoints) and a vector index for about 10³–10⁵ help-centre chunks.

## Decision
One Azure Database for PostgreSQL Flexible Server holds both: the LangGraph `AsyncPostgresSaver` tables, and `kb_chunks` using **pgvector (HNSW)** plus a generated **tsvector** column for hybrid search.

## Consequences
- **+** One stateful service to operate, back up, secure and pay for. Point-in-time restore covers conversations and KB together.
- **+** Hybrid search (vector + full-text, fused with RRF) runs in a single SQL query, with no second system.
- **+** Transactions and idempotent upserts for ingestion.
- **−** At very large scale (millions of chunks, high QPS) a dedicated engine (Azure AI Search) would win on relevance features such as semantic ranking. The `Retriever` protocol makes that a one-class swap.
- **−** The vector dimension is fixed in the migration (1536). Changing embedding models needs a new migration and a re-ingest.
