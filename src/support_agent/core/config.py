"""Typed application settings.

Every tunable lives here, loaded from env vars (12-factor). Nothing else in the
codebase reads os.environ directly - that rule is what makes config auditable.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class CheckpointerBackend(StrEnum):
    MEMORY = "memory"
    POSTGRES = "postgres"


class RetrieverBackend(StrEnum):
    MEMORY = "memory"
    PGVECTOR = "pgvector"


class RerankerBackend(StrEnum):
    NONE = "none"  # keep hybrid-search (RRF) order
    COHERE = "cohere"


class ModelPrice(BaseModel):
    """USD per 1M tokens."""

    input: float
    cached_input: float
    output: float


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App -------------------------------------------------------------
    app_name: str = "support-agent"
    environment: Environment = Environment.LOCAL
    log_level: str = "INFO"
    log_json: bool = True

    # --- Auth (comma-separated keys; stored in Key Vault in Azure) ---------
    client_api_keys: SecretStr = SecretStr("")
    staff_api_keys: SecretStr = SecretStr("")

    # --- OpenAI -------------------------------------------------------------
    # Required everywhere except tests (enforced below). Key Vault in Azure.
    openai_api_key: SecretStr | None = None
    # Optional: an internal LLM gateway / proxy speaking the OpenAI API.
    openai_base_url: str | None = None
    openai_chat_model: str = "gpt-4.1-mini"
    # Different model (or same model via another gateway) used when the primary
    # keeps failing after retries.
    openai_fallback_model: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"
    openai_moderation_model: str = "omni-moderation-latest"
    embedding_dimensions: int = 1536
    # None for reasoning models that reject the temperature param.
    llm_temperature: float | None = 0.0
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 3

    # USD per 1M tokens, matched by longest model-name prefix ("gpt-4.1-mini"
    # matches "gpt-4.1-mini-2025-04-14"). Prices change: verify against
    # https://openai.com/api/pricing and override via MODEL_PRICING (JSON).
    model_pricing: dict[str, ModelPrice] = Field(
        default_factory=lambda: {
            "gpt-4.1-mini": ModelPrice(input=0.40, cached_input=0.10, output=1.60),
            "gpt-4.1": ModelPrice(input=2.00, cached_input=0.50, output=8.00),
            "gpt-4o-mini": ModelPrice(input=0.15, cached_input=0.075, output=0.60),
        }
    )

    # --- Persistence --------------------------------------------------------
    database_url: SecretStr = SecretStr(
        "postgresql://postgres:postgres@localhost:5432/support_agent"
    )
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10
    checkpointer_backend: CheckpointerBackend = CheckpointerBackend.POSTGRES
    retriever_backend: RetrieverBackend = RetrieverBackend.PGVECTOR
    run_migrations_on_startup: bool = True

    # --- Semantic cache (FAQ answers only) ---------------------------------
    semantic_cache_enabled: bool = True
    # Cosine similarity needed to reuse an answer. High on purpose: a wrong
    # cache hit is a confidently wrong answer.
    semantic_cache_threshold: float = Field(default=0.95, ge=0.5, le=1.0)
    semantic_cache_ttl_hours: int = 24

    # --- Reranking --------------------------------------------------------
    reranker_backend: RerankerBackend = RerankerBackend.NONE
    cohere_api_key: SecretStr | None = None
    cohere_rerank_model: str = "rerank-v3.5"
    rerank_candidates: int = 20  # retrieve this many, rerank down to retrieval_top_k

    # --- Moderation ---------------------------------------------------------
    # OpenAI moderation on customer input and on every customer-facing answer.
    enable_moderation: bool = True

    # --- Agent behaviour ----------------------------------------------------
    intent_confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    retrieval_top_k: int = 5
    retrieval_candidates: int = 20
    max_query_rewrites: int = 1
    max_tool_iterations: int = 5
    refund_auto_approve_limit: float = 50.0
    enable_groundedness_check: bool = True
    graph_recursion_limit: int = 25
    max_input_chars: int = 4000
    history_window_messages: int = 12

    # --- Integrations (None => in-memory fakes) ------------------------------
    order_service_base_url: str | None = None
    ticketing_base_url: str | None = None
    http_timeout_seconds: float = 10.0

    # --- Observability ------------------------------------------------------
    applicationinsights_connection_string: SecretStr | None = None

    @field_validator(
        "openai_api_key",
        "openai_base_url",
        "openai_fallback_model",
        "cohere_api_key",
        "order_service_base_url",
        "ticketing_base_url",
        "applicationinsights_connection_string",
        mode="before",
    )
    @classmethod
    def _empty_str_is_none(cls, value: object) -> object:
        # `FOO=` in an env file means "unset", not "empty string".
        return None if value == "" else value

    @model_validator(mode="after")
    def _require_secrets_outside_dev(self) -> Settings:
        # Fail at boot, not on the first customer message.
        if self.environment != Environment.TEST and self.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required (set it in .env or Key Vault).")
        if self.reranker_backend == RerankerBackend.COHERE and self.cohere_api_key is None:
            raise ValueError("COHERE_API_KEY is required when RERANKER_BACKEND=cohere.")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION

    @property
    def client_keys(self) -> frozenset[str]:
        return _split_keys(self.client_api_keys)

    @property
    def staff_keys(self) -> frozenset[str]:
        return _split_keys(self.staff_api_keys)


def _split_keys(raw: SecretStr) -> frozenset[str]:
    return frozenset(k.strip() for k in raw.get_secret_value().split(",") if k.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
