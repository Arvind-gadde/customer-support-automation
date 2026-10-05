"""Print the compiled graph as Mermaid. Builds with dummy creds: no network calls."""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from support_agent.agent.graph import build_graph
from support_agent.agent.tools import build_tools
from support_agent.core.config import Settings
from support_agent.integrations.orders import InMemoryOrderService
from support_agent.integrations.ticketing import InMemoryTicketingClient
from support_agent.llm.factory import build_dependencies
from support_agent.rag.retriever import InMemoryRetriever

if __name__ == "__main__":
    settings = Settings(
        _env_file=None,
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_api_key="unused",
    )
    deps = build_dependencies(
        settings,
        tools=build_tools(InMemoryOrderService()),
        retriever=InMemoryRetriever([]),
        ticketing=InMemoryTicketingClient(),
    )
    print(build_graph(deps, InMemorySaver()).get_graph().draw_mermaid())
