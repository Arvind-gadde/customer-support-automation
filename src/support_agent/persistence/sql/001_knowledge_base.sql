-- Knowledge base for RAG. On Azure Database for PostgreSQL Flexible Server the
-- VECTOR extension must be allow-listed via the `azure.extensions` server
-- parameter (done in infra/modules/postgres.bicep).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS kb_chunks (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    title         TEXT NOT NULL,
    chunk_index   INT  NOT NULL,
    content       TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    -- 1536 = text-embedding-3-small. Changing models => new migration + re-ingest.
    embedding     vector(1536) NOT NULL,
    search_tsv    tsvector GENERATED ALWAYS AS (
                      setweight(to_tsvector('english', title), 'A') ||
                      setweight(to_tsvector('english', content), 'B')
                  ) STORED,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HNSW: better recall/latency than IVFFlat and no training step.
CREATE INDEX IF NOT EXISTS kb_chunks_embedding_hnsw
    ON kb_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_chunks_search_tsv_gin ON kb_chunks USING gin (search_tsv);
CREATE INDEX IF NOT EXISTS kb_chunks_source_idx ON kb_chunks (source);
