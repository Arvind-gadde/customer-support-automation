"""Typed application settings.

Every tunable lives here, loaded from env vars (12-factor). Nothing else in the
codebase reads os.environ directly - that rule is what makes config auditable.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
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

    # --- Azure OpenAI -------------------------------------------------------
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-10-21"
    # None => Entra ID auth (managed identity in Azure, `az login` locally).
    azure_openai_api_key: SecretStr | None = None
    azure_openai_chat_deployment: str = "gpt-4.1-mini"
    azure_openai_fallback_deployment: str | None = None
    azure_openai_embedding_deployment: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    # None for reasoning models that reject the temperature param.
    llm_temperature: float | None = 0.0
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 3

    # --- Persistence --------------------------------------------------------
    database_url: SecretStr = SecretStr(
        "postgresql://postgres:postgres@localhost:5432/support_agent"
    )
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10
    checkpointer_backend: CheckpointerBackend = CheckpointerBackend.POSTGRES
    retriever_backend: RetrieverBackend = RetrieverBackend.PGVECTOR
    run_migrations_on_startup: bool = True

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
        "azure_openai_api_key",
        "azure_openai_fallback_deployment",
        "order_service_base_url",
        "ticketing_base_url",
        "applicationinsights_connection_string",
        mode="before",
    )
    @classmethod
    def _empty_str_is_none(cls, value: object) -> object:
        # `FOO=` in an env file means "unset", not "empty string".
        return None if value == "" else value

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
