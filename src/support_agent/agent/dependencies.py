"""Everything the graph needs, injected at build time.

Nodes depend on Runnables and Protocols, not on AzureChatOpenAI or Postgres.
That single decision is what makes the graph unit-testable with fakes and lets
us swap models/providers without touching node code.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from support_agent.agent.schemas import (
    GroundednessVerdict,
    IntentDecision,
    RelevanceGrade,
    RewrittenQuery,
)
from support_agent.core.config import Settings
from support_agent.integrations.ticketing import TicketingClient
from support_agent.rag.retriever import Retriever

Messages = list[BaseMessage]


@dataclass(frozen=True, slots=True)
class AgentDependencies:
    settings: Settings
    router: Runnable[Messages, IntentDecision]
    grader: Runnable[Messages, RelevanceGrade]
    rewriter: Runnable[Messages, RewrittenQuery]
    groundedness: Runnable[Messages, GroundednessVerdict]
    answerer: Runnable[Messages, AIMessage]
    account_agent: Runnable[Messages, AIMessage]  # already bound to `tools`
    tools: list[BaseTool]
    retriever: Retriever
    ticketing: TicketingClient
