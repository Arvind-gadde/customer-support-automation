"""Print the compiled graph as Mermaid. Builds with dummy creds: no network calls."""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from support_agent.agent.graph import build_graph
from support_agent.agent.tools import build_tools
from support_agent.core.config import Settings
from support_agent.integrations.orders import InMemoryOrderService
from support_agent.integrations.ticketing import InMemoryTicketingClient
from support_agent.llm.factory import build_dependencies
from support_agent.llm.moderation import NoopModerator
from support_agent.rag.reranker import PassthroughReranker
from support_agent.rag.retriever import InMemoryRetriever
from support_agent.rag.semantic_cache import NullSemanticCache

if __name__ == "__main__":
    settings = Settings(
        _env_file=None,
        openai_api_key="sk-unused-no-calls-are-made",
    )
    deps = build_dependencies(
        settings,
        tools=build_tools(InMemoryOrderService()),
        retriever=InMemoryRetriever([]),
        reranker=PassthroughReranker(),
        cache=NullSemanticCache(),
        moderator=NoopModerator(),
        ticketing=InMemoryTicketingClient(),
    )
    print(build_graph(deps, InMemorySaver()).get_graph().draw_mermaid())
