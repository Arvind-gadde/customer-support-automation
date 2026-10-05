"""Input guardrails node + the canned reply for blocked input."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from support_agent.agent.dependencies import AgentDependencies
from support_agent.agent.guardrails import detect_prompt_injection, redact_pii
from support_agent.agent.messages import last_human_text
from support_agent.agent.prompts import BLOCKED_REPLY
from support_agent.agent.state import AgentState
from support_agent.core.logging import get_logger
from support_agent.core.telemetry import traced_node

logger = get_logger(__name__)


def make_guard_input(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    max_chars = deps.settings.max_input_chars

    @traced_node("guard_input")
    async def guard_input(state: AgentState) -> dict[str, Any]:
        text = last_human_text(state["messages"])
        violation: str | None = None
        if len(text) > max_chars:
            violation = "input_too_long"
        elif detect_prompt_injection(text):
            violation = "prompt_injection"

        if violation:
            logger.warning("guardrail.blocked", violation=violation, preview=redact_pii(text[:200]))
        return {
            "guardrail_violation": violation,
            "audit_log": [{"event": "guardrail_blocked", "violation": violation}]
            if violation
            else [],
        }

    return guard_input


@traced_node("blocked_reply")
async def blocked_reply(state: AgentState) -> dict[str, Any]:
    return {"messages": [AIMessage(content=BLOCKED_REPLY)]}
