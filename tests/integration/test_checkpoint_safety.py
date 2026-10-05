"""Guards against custom Python types leaking into checkpoints.

LangGraph is moving to block deserialisation of unregistered types. If a node
writes e.g. an Enum into state, today it works with a warning; after an upgrade
every paused conversation (pending approvals, open handoffs) would fail to load.
So: state must only ever contain JSON-ish primitives (plus LangChain messages).
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage

from support_agent.services.schemas import ChatRequest

PRIMITIVES = (str, int, float, bool, type(None))


def _custom_types(value: Any, path: str = "state") -> list[str]:
    if isinstance(value, BaseMessage):
        return []  # LangChain messages are serialised by LangGraph natively
    if type(value) in PRIMITIVES:
        return []
    if type(value) is dict:
        return [p for k, v in value.items() for p in _custom_types(v, f"{path}.{k}")]
    if type(value) is list:
        return [p for i, v in enumerate(value) for p in _custom_types(v, f"{path}[{i}]")]
    return [f"{path}: {type(value).__module__}.{type(value).__qualname__}"]


async def test_checkpointed_state_contains_only_primitive_types(service_factory) -> None:
    service = service_factory()
    # Cover every branch that writes routing/handoff fields.
    messages = [
        "What is the return window?",  # faq
        "hello",  # smalltalk
        "Where is my order ORD-1002?",  # account + tools
        "maybe something",  # low confidence -> handoff
    ]
    thread_ids = []
    for message in messages:
        turn = await service.chat(ChatRequest(customer_id="cust_123", message=message))
        thread_ids.append(turn.thread_id)

    leaks: list[str] = []
    for thread_id in thread_ids:
        snapshot = await service._graph.aget_state({"configurable": {"thread_id": thread_id}})
        leaks += _custom_types(dict(snapshot.values))
    assert not leaks, f"Non-primitive values in checkpointed state: {leaks}"
