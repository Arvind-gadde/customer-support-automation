"""Intent classification. Confidence-gated: unsure => human, never a guess."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from support_agent.agent.dependencies import AgentDependencies
from support_agent.agent.messages import transcript
from support_agent.agent.prompts import ROUTER_SYSTEM
from support_agent.agent.state import AgentState, HandoffReason, Intent
from support_agent.core.logging import get_logger
from support_agent.core.telemetry import set_span_attributes, traced_node
from support_agent.llm.errors import is_content_filter_error

logger = get_logger(__name__)


def make_classify_intent(deps: AgentDependencies):  # type: ignore[no-untyped-def]
    settings = deps.settings

    @traced_node("classify_intent")
    async def classify_intent(state: AgentState) -> dict[str, Any]:
        convo = transcript(state["messages"], settings.history_window_messages)
        try:
            decision = await deps.router.ainvoke(
                [SystemMessage(ROUTER_SYSTEM), HumanMessage(f"Conversation:\n{convo}")]
            )
        except Exception as exc:
            if not is_content_filter_error(exc):
                raise
            return {
                "intent": Intent.HUMAN.value,
                "handoff_reason": HandoffReason.CONTENT_FILTERED.value,
            }

        set_span_attributes(
            **{"agent.intent": decision.intent.value, "agent.confidence": decision.confidence}
        )
        logger.info("intent.classified", intent=decision.intent, confidence=decision.confidence)

        if decision.intent == Intent.HUMAN:
            return {
                "intent": Intent.HUMAN.value,
                "handoff_reason": HandoffReason.CUSTOMER_REQUEST.value,
            }
        if decision.confidence < settings.intent_confidence_threshold:
            return {
                "intent": Intent.HUMAN.value,
                "handoff_reason": HandoffReason.LOW_CONFIDENCE.value,
            }
        return {"intent": decision.intent.value}

    return classify_intent
