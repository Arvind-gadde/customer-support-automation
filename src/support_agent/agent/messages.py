"""Helpers for shaping conversation history before it goes to a model."""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage, trim_messages


def last_human_text(messages: Sequence[AnyMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message.text
    return ""


def dialogue(messages: Sequence[AnyMessage], window: int) -> list[BaseMessage]:
    """Human turns + final AI replies only (no tool-call plumbing), last `window` messages."""
    kept = [
        m
        for m in messages
        if isinstance(m, HumanMessage) or (isinstance(m, AIMessage) and not m.tool_calls)
    ]
    return list(kept[-window:])


def transcript(messages: Sequence[AnyMessage], window: int) -> str:
    lines = []
    for m in dialogue(messages, window):
        role = "Customer" if isinstance(m, HumanMessage) else "Assistant"
        lines.append(f"{role}: {m.text}")
    return "\n".join(lines)


def tool_conversation(messages: Sequence[AnyMessage], window: int) -> list[BaseMessage]:
    """Recent history INCLUDING tool calls/results, trimmed without orphaning a tool message.

    start_on="human" guarantees we never begin mid tool-call sequence, which the
    OpenAI API rejects ("tool message must follow assistant tool_calls").
    """
    return trim_messages(
        list(messages),
        strategy="last",
        token_counter=len,  # count messages, not tokens: cheap and predictable
        max_tokens=window,
        start_on="human",
        include_system=False,
        allow_partial=False,
    )
