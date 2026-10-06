"""Guardrails on the way in (guard_input) and on the way out (guard_output).

Input:  length limit -> regex injection check -> OpenAI moderation. Cheapest first.
Output: every customer-facing answer passes here before the turn ends:
        system-prompt leak check -> moderation -> PII redaction.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from support_agent.agent.dependencies import AgentDependencies
from support_agent.agent.guardrails import detect_prompt_injection, leaks_system_prompt, redact_pii
from support_agent.agent.messages import last_human_text
from support_agent.agent.prompts import BLOCKED_REPLY, OUTPUT_BLOCKED_REPLY, SYSTEM_PROMPTS
from support_agent.agent.state import AgentState, HandoffReason
from support_agent.core.logging import get_logger
from support_agent.core.telemetry import traced_node

logger = get_logger(__name__)


def make_guard_input(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    max_chars = deps.settings.max_input_chars
    moderate = deps.settings.enable_moderation

    @traced_node("guard_input")
    async def guard_input(state: AgentState) -> dict[str, Any]:
        text = last_human_text(state["messages"])
        violation: str | None = None
        categories: list[str] = []
        if len(text) > max_chars:
            violation = "input_too_long"
        elif detect_prompt_injection(text):
            violation = "prompt_injection"
        elif moderate:
            result = await deps.moderator.check(text)
            if result.flagged:
                violation, categories = "moderation_flagged", result.categories

        if violation is None:
            return {"guardrail_violation": None}
        logger.warning(
            "guardrail.blocked",
            violation=violation,
            categories=categories,
            preview=redact_pii(text[:200]),
        )
        return {
            "guardrail_violation": violation,
            "audit_log": [
                {"event": "input_blocked", "violation": violation, "categories": categories}
            ],
        }

    return guard_input


@traced_node("blocked_reply")
async def blocked_reply(state: AgentState) -> dict[str, Any]:
    return {"messages": [AIMessage(content=BLOCKED_REPLY)]}


def make_guard_output(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    moderate = deps.settings.enable_moderation

    def _block(last: AIMessage, reason: str, **details: Any) -> dict[str, Any]:
        # Same id => add_messages REPLACES the unsafe message instead of appending,
        # so it never survives in the checkpoint either.
        return {
            "messages": [AIMessage(id=last.id, content=OUTPUT_BLOCKED_REPLY)],
            "output_modified": True,
            "handoff_reason": HandoffReason.OUTPUT_FLAGGED.value,
            "audit_log": [{"event": "output_blocked", "reason": reason, **details}],
        }

    @traced_node("guard_output")
    async def guard_output(state: AgentState) -> dict[str, Any]:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or last.tool_calls:
            return {}
        text = last.text

        if leaks_system_prompt(text, SYSTEM_PROMPTS):
            logger.error("output_guard.prompt_leak")
            return _block(last, "prompt_leak")

        if moderate:
            result = await deps.moderator.check(text)
            if result.flagged:
                logger.error("output_guard.moderation_flagged", categories=result.categories)
                return _block(last, "moderation", categories=result.categories)

        redacted = redact_pii(text)
        if redacted != text:
            logger.warning("output_guard.pii_redacted")
            return {
                "messages": [
                    AIMessage(
                        id=last.id, content=redacted, response_metadata=last.response_metadata
                    )
                ],
                "output_modified": True,
                "audit_log": [{"event": "output_redacted", "reason": "pii"}],
            }
        return {}

    return guard_output
