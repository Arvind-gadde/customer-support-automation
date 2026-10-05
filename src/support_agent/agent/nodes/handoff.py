"""Human handoff, split into two nodes on purpose.

interrupt() re-executes its node from the top when the graph resumes. If ticket
creation and the interrupt lived in one node, we'd create a duplicate ticket on
every resume. Side effect in `create_ticket`, pause in `await_human` - and the
ticket call carries an idempotency key as a second line of defence.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from support_agent.agent.dependencies import AgentDependencies
from support_agent.agent.guardrails import redact_pii
from support_agent.agent.messages import transcript
from support_agent.agent.prompts import HANDOFF_REPLY
from support_agent.agent.state import AgentState, HandoffReason
from support_agent.core.logging import get_logger
from support_agent.core.telemetry import traced_node

logger = get_logger(__name__)


def make_create_ticket(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    @traced_node("create_ticket")
    async def create_ticket(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        thread_id = config["configurable"]["thread_id"]
        reason = state.get("handoff_reason") or HandoffReason.CUSTOMER_REQUEST.value
        ticket = await deps.ticketing.create_ticket(
            customer_id=state["customer_id"],
            reason=str(reason),
            transcript=redact_pii(transcript(state["messages"], window=20)),
            idempotency_key=f"{thread_id}:{len(state['messages'])}",
        )
        logger.info("handoff.ticket_created", ticket_id=ticket.ticket_id, reason=str(reason))
        return {
            "ticket_id": ticket.ticket_id,
            "audit_log": [{"event": "handoff", "reason": str(reason), "ticket": ticket.ticket_id}],
        }

    return create_ticket


@traced_node("await_human")
async def await_human(state: AgentState) -> dict[str, Any]:
    reply = interrupt(
        {
            "type": "handoff",
            "ticket_id": state.get("ticket_id"),
            "reason": str(state.get("handoff_reason")),
            "customer_message": HANDOFF_REPLY.format(ticket_id=state.get("ticket_id")),
        }
    )
    return {
        "messages": [
            AIMessage(
                content=reply["message"],
                name="human_agent",
                response_metadata={"author": reply.get("agent_name"), "source": "human"},
            )
        ],
        "handoff_reason": None,
    }
