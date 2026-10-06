-- Semantic answer cache (see rag/semantic_cache.py for the correctness rules).
CREATE TABLE IF NOT EXISTS semantic_cache (
    id          TEXT PRIMARY KEY,           -- sha256(namespace + normalised question)
    namespace   TEXT NOT NULL,              -- prompt version + model
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    sources     TEXT[] NOT NULL DEFAULT '{}',
    embedding   vector(1536) NOT NULL,
    hits        INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS semantic_cache_embedding_hnsw
    ON semantic_cache USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS semantic_cache_namespace_created_idx
    ON semantic_cache (namespace, created_at);
