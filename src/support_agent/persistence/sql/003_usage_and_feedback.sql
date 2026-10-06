-- Per-turn LLM token usage and cost, one row per model used in the turn.
CREATE TABLE IF NOT EXISTS llm_usage (
    id                   BIGSERIAL PRIMARY KEY,
    thread_id            TEXT NOT NULL,
    request_id           TEXT,
    model                TEXT NOT NULL,
    calls                INT NOT NULL,
    input_tokens         INT NOT NULL,
    cached_input_tokens  INT NOT NULL,
    output_tokens        INT NOT NULL,
    cost_usd             NUMERIC(12, 6) NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS llm_usage_thread_idx ON llm_usage (thread_id);
CREATE INDEX IF NOT EXISTS llm_usage_created_idx ON llm_usage (created_at);

-- Customer thumbs up/down on individual assistant messages. One vote per
-- message; voting again overwrites (customers change their minds).
CREATE TABLE IF NOT EXISTS feedback (
    thread_id       TEXT NOT NULL,
    message_id      TEXT NOT NULL,
    customer_id     TEXT NOT NULL,
    rating          SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
    comment         TEXT,
    prompt_version  TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (thread_id, message_id)
);
CREATE INDEX IF NOT EXISTS feedback_created_idx ON feedback (created_at);
