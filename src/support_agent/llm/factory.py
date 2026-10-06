"""Model construction. The ONLY module that knows the provider is OpenAI.

Swapping providers (Azure OpenAI, an internal gateway, another vendor with an
OpenAI-compatible API) means changing this file and nothing else.

Resilience, in order:
  1. SDK retries with exponential backoff (429 / 5xx / timeouts).
  2. Optional fallback model, used when the primary still fails.
  3. The service layer turns what's left into a retryable 503.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel

from support_agent.agent.dependencies import AgentDependencies, Messages
from support_agent.agent.schemas import (
    GroundednessVerdict,
    IntentDecision,
    RelevanceGrade,
    RewrittenQuery,
)
from support_agent.core.config import Settings
from support_agent.integrations.ticketing import TicketingClient
from support_agent.llm.moderation import Moderator
from support_agent.rag.reranker import Reranker
from support_agent.rag.retriever import Retriever
from support_agent.rag.semantic_cache import SemanticCache

S = TypeVar("S", bound=BaseModel)


def _client_kwargs(settings: Settings) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "max_retries": settings.llm_max_retries,
        "timeout": settings.llm_timeout_seconds,
    }
    if settings.openai_api_key is not None:
        kwargs["api_key"] = settings.openai_api_key
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return kwargs


def build_chat_model(settings: Settings, model: str) -> ChatOpenAI:
    kwargs: dict[str, Any] = {
        "model": model,
        # Token usage on streamed responses too, so cost tracking sees every call.
        "stream_usage": True,
        **_client_kwargs(settings),
    }
    if settings.llm_temperature is not None:
        kwargs["temperature"] = settings.llm_temperature
    return ChatOpenAI(**kwargs)


def build_embeddings(settings: Settings) -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=settings.openai_embedding_model,
        dimensions=settings.embedding_dimensions,
        **_client_kwargs(settings),
    )


def _with_fallback(
    primary: BaseChatModel,
    fallback: BaseChatModel | None,
    adapt: Callable[[BaseChatModel], Runnable[Messages, Any]],
) -> Runnable[Messages, Any]:
    runnable = adapt(primary)
    return runnable.with_fallbacks([adapt(fallback)]) if fallback else runnable


def build_dependencies(
    settings: Settings,
    *,
    tools: list[BaseTool],
    retriever: Retriever,
    reranker: Reranker,
    cache: SemanticCache,
    moderator: Moderator,
    ticketing: TicketingClient,
) -> AgentDependencies:
    primary = build_chat_model(settings, settings.openai_chat_model)
    fallback = (
        build_chat_model(settings, settings.openai_fallback_model)
        if settings.openai_fallback_model
        else None
    )

    def structured(schema: type[S]) -> Runnable[Messages, S]:
        return _with_fallback(
            primary,
            fallback,
            lambda m: m.with_structured_output(schema, method="json_schema", strict=True),
        )

    answerer: Runnable[Messages, AIMessage] = _with_fallback(primary, fallback, lambda m: m)
    account_agent: Runnable[Messages, AIMessage] = _with_fallback(
        primary, fallback, lambda m: m.bind_tools(tools, parallel_tool_calls=False)
    )

    return AgentDependencies(
        settings=settings,
        router=structured(IntentDecision),
        grader=structured(RelevanceGrade),
        rewriter=structured(RewrittenQuery),
        groundedness=structured(GroundednessVerdict),
        answerer=answerer,
        account_agent=account_agent,
        tools=tools,
        retriever=retriever,
        reranker=reranker,
        cache=cache,
        moderator=moderator,
        ticketing=ticketing,
    )
