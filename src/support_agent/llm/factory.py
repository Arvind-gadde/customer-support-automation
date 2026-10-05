"""Model construction. The ONLY module that knows we're on Azure OpenAI.

- Auth: Entra ID token provider by default (managed identity in Container Apps,
  `az login` locally). API keys are supported but discouraged; infra disables
  key auth on the OpenAI resource entirely.
- Resilience: SDK-level retries with backoff, plus an optional fallback
  deployment (e.g. a second region or a different model) for 429/5xx storms.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
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
from support_agent.rag.retriever import Retriever

_COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
S = TypeVar("S", bound=BaseModel)


def _auth_kwargs(settings: Settings) -> dict[str, Any]:
    if settings.azure_openai_api_key is not None:
        return {"api_key": settings.azure_openai_api_key}
    return {
        "azure_ad_token_provider": get_bearer_token_provider(
            DefaultAzureCredential(), _COGNITIVE_SCOPE
        )
    }


def build_chat_model(settings: Settings, deployment: str) -> AzureChatOpenAI:
    kwargs: dict[str, Any] = {
        "azure_endpoint": settings.azure_openai_endpoint,
        "azure_deployment": deployment,
        "api_version": settings.azure_openai_api_version,
        "timeout": settings.llm_timeout_seconds,
        "max_retries": settings.llm_max_retries,
        **_auth_kwargs(settings),
    }
    if settings.llm_temperature is not None:
        kwargs["temperature"] = settings.llm_temperature
    return AzureChatOpenAI(**kwargs)


def build_embeddings(settings: Settings) -> AzureOpenAIEmbeddings:
    return AzureOpenAIEmbeddings(
        azure_endpoint=settings.azure_openai_endpoint,
        deployment=settings.azure_openai_embedding_deployment,
        openai_api_version=settings.azure_openai_api_version,
        dimensions=settings.embedding_dimensions,
        max_retries=settings.llm_max_retries,
        **_auth_kwargs(settings),
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
    ticketing: TicketingClient,
) -> AgentDependencies:
    primary = build_chat_model(settings, settings.azure_openai_chat_deployment)
    fallback = (
        build_chat_model(settings, settings.azure_openai_fallback_deployment)
        if settings.azure_openai_fallback_deployment
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
        ticketing=ticketing,
    )
