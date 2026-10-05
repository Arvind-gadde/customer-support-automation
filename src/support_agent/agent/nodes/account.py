"""Tool-calling account agent with human-in-the-loop approval for risky actions.

account_agent --(tool_calls)--> review_tool_calls --approved--> tools --> account_agent
                                                   --rejected--> account_agent
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.types import Command, interrupt

from support_agent.agent.dependencies import AgentDependencies
from support_agent.agent.messages import tool_conversation
from support_agent.agent.policies import requires_approval
from support_agent.agent.prompts import ACCOUNT_AGENT_SYSTEM, APPROVAL_PENDING_REPLY
from support_agent.agent.state import AgentState, HandoffReason
from support_agent.core.logging import get_logger
from support_agent.core.telemetry import traced_node

logger = get_logger(__name__)


def make_account_agent(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    settings = deps.settings

    @traced_node("account_agent")
    async def account_agent(state: AgentState) -> dict[str, Any]:
        iterations = state.get("tool_iterations", 0)
        if iterations >= settings.max_tool_iterations:
            # Guard against tool-call loops (model keeps calling tools forever).
            logger.warning("account_agent.iteration_limit", iterations=iterations)
            return {"handoff_reason": HandoffReason.TOOL_ITERATION_LIMIT.value}

        history = tool_conversation(state["messages"], window=settings.history_window_messages * 2)
        response = await deps.account_agent.ainvoke([SystemMessage(ACCOUNT_AGENT_SYSTEM), *history])
        return {"messages": [response], "tool_iterations": iterations + 1}

    return account_agent


def make_review_tool_calls(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    limit = deps.settings.refund_auto_approve_limit

    @traced_node("review_tool_calls")
    async def review_tool_calls(state: AgentState) -> Command[Literal["tools", "account_agent"]]:
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        sensitive = [tc for tc in last.tool_calls if requires_approval(tc, limit)]
        if not sensitive:
            return Command(goto="tools")

        # NOTE: interrupt() re-runs this whole node on resume. Everything above
        # this line must be side-effect free (it is: pure reads + a policy check).
        decision = interrupt(
            {
                "type": "approval",
                "customer_id": state["customer_id"],
                "actions": [{"tool": tc["name"], "args": tc["args"]} for tc in sensitive],
                "customer_message": APPROVAL_PENDING_REPLY,
            }
        )
        approved = bool(decision.get("approved"))
        audit = {
            "event": "approval_decision",
            "approved": approved,
            "reviewer": decision.get("reviewer"),
            "note": decision.get("note"),
            "actions": [tc["name"] for tc in sensitive],
        }
        logger.info("approval.decided", approved=approved, reviewer=decision.get("reviewer"))
        if approved:
            return Command(goto="tools", update={"audit_log": [audit]})

        # OpenAI requires a ToolMessage for EVERY tool_call_id, so reject all of them.
        note = decision.get("note") or "No reason given."
        rejections = [
            ToolMessage(
                content=f"Action not performed: rejected by a support reviewer. Reason: {note}",
                tool_call_id=tc["id"],
                name=tc["name"],
                status="error",
            )
            for tc in last.tool_calls
        ]
        return Command(goto="account_agent", update={"messages": rejections, "audit_log": [audit]})

    return review_tool_calls
