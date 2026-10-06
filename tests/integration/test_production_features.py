"""Semantic cache, reranking, moderation, output guard, cost tracking, feedback."""

from __future__ import annotations

from decimal import Decimal

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from support_agent.agent.prompts import ROUTER_SYSTEM
from support_agent.agent.schemas import GroundednessVerdict, RelevanceGrade
from support_agent.core.errors import NotFoundError
from support_agent.persistence.stores import InMemoryFeedbackStore, InMemoryUsageStore
from support_agent.rag.semantic_cache import InMemorySemanticCache
from support_agent.services.schemas import ChatRequest, FeedbackRequest, TurnStatus
from tests.fakes import KeywordModerator

CUSTOMER = "cust_123"


def ask(message: str, thread_id: str | None = None) -> ChatRequest:
    return ChatRequest(customer_id=CUSTOMER, message=message, thread_id=thread_id)


class CountingAnswerer:
    def __init__(self, text: str = "Returns are accepted within 10 days.") -> None:
        self.calls = 0
        self.text = text

    def __call__(self, messages):  # type: ignore[no-untyped-def]
        self.calls += 1
        return AIMessage(content=self.text)


# ------------------------------------------------------------------ semantic cache


async def test_repeat_question_is_served_from_cache(service_factory) -> None:
    answerer = CountingAnswerer()
    service = service_factory(answerer=answerer)

    first = await service.chat(ask("What is the return window?"))
    second = await service.chat(ask("what is the RETURN window??"))  # new thread

    assert (first.cached, second.cached) == (False, True)
    assert second.reply == first.reply
    assert answerer.calls == 1  # second answer cost zero LLM generations


async def test_follow_up_turns_never_use_the_cache(service_factory) -> None:
    answerer = CountingAnswerer()
    service = service_factory(answerer=answerer)
    first = await service.chat(ask("What is the return window?"))
    again = await service.chat(ask("What is the return window?", thread_id=first.thread_id))
    assert not again.cached
    assert answerer.calls == 2


async def test_ungrounded_answers_are_never_cached(service_factory) -> None:
    verdicts = iter([False, True])
    answerer = CountingAnswerer()
    service = service_factory(
        answerer=answerer,
        groundedness=lambda m: GroundednessVerdict(grounded=next(verdicts), unsupported_claims=[]),
    )
    rejected = await service.chat(ask("What is the return window?"))
    retried = await service.chat(ask("What is the return window?"))
    assert rejected.status == TurnStatus.AWAITING_HUMAN
    assert not retried.cached  # the rejected draft was not stored
    assert answerer.calls == 2


async def test_account_answers_are_never_cached(service_factory) -> None:
    cache = InMemorySemanticCache(namespace="test")
    await service_factory(cache=cache).chat(ask("Where is my order ORD-1002?"))
    assert cache._entries == {}


async def test_broken_cache_never_fails_a_turn(service_factory) -> None:
    class BrokenCache:
        async def lookup(self, question: str) -> None:
            raise ConnectionError("cache down")

        async def store(self, question: str, answer: str, sources: list[str]) -> None:
            raise ConnectionError("cache down")

    result = await service_factory(cache=BrokenCache()).chat(ask("What is the return window?"))
    assert result.status == TurnStatus.COMPLETED


# ----------------------------------------------------------------------- reranking


async def test_reranker_output_is_what_the_grader_sees(service_factory) -> None:
    seen: list[str] = []

    class KeepLast:
        async def rerank(self, query, docs, top_n):  # type: ignore[no-untyped-def]
            return docs[-1:]

    def grader(messages):  # type: ignore[no-untyped-def]
        seen.append(messages[-1].text)
        return RelevanceGrade(relevant_doc_ids=[])

    await service_factory(reranker=KeepLast(), grader=grader).chat(ask("return window refund"))
    assert seen
    assert seen[0].count("<doc id=") == 1


# ---------------------------------------------------------- moderation + output guard


async def test_flagged_input_is_blocked_before_routing(service_factory) -> None:
    def router_must_not_run(_):  # type: ignore[no-untyped-def]
        raise AssertionError("router ran")

    service = service_factory(moderator=KeywordModerator("idiot"), router=router_must_not_run)
    result = await service.chat(ask("you idiot, where is my refund"))
    assert "can't help" in (result.reply or "")


async def test_flagged_output_is_replaced_and_escalated(service_factory) -> None:
    service = service_factory(
        moderator=KeywordModerator("idiot"),
        answerer=lambda m: AIMessage(content="Only an idiot would ask that."),
    )
    result = await service.chat(ask("What is the return window?"))
    assert result.status == TurnStatus.AWAITING_HUMAN
    thread = await service.get_thread(result.thread_id, CUSTOMER)
    assert all("idiot" not in m.content for m in thread.messages)  # gone from history too


async def test_system_prompt_leak_is_blocked(service_factory) -> None:
    service = service_factory(answerer=lambda m: AIMessage(content=f"Sure! {ROUTER_SYSTEM}"))
    result = await service.chat(ask("What is the return window?"))
    assert result.status == TurnStatus.AWAITING_HUMAN
    thread = await service.get_thread(result.thread_id, CUSTOMER)
    assert all("You route messages" not in m.content for m in thread.messages)


async def test_pii_is_redacted_and_redacted_answers_are_not_cached(service_factory) -> None:
    cache = InMemorySemanticCache(namespace="test")
    service = service_factory(
        cache=cache,
        answerer=lambda m: AIMessage(content="Write to help@acme.in, card 4111 1111 1111 1111."),
    )
    result = await service.chat(ask("What is the return window?"))
    assert result.status == TurnStatus.COMPLETED
    assert "[EMAIL]" in (result.reply or "")
    assert "[CARD]" in (result.reply or "")
    assert cache._entries == {}


# ------------------------------------------------------------------- cost tracking


async def test_token_usage_and_cost_are_recorded_per_turn(service_factory, settings) -> None:
    usage = {
        "input_tokens": 1000,
        "output_tokens": 200,
        "total_tokens": 1200,
        "input_token_details": {"cache_read": 400},
    }
    model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="Returns are accepted within 10 days.",
                    usage_metadata=usage,
                    response_metadata={"model_name": "gpt-4.1-mini-2025-04-14"},
                )
            ]
        )
    )
    service = service_factory(answerer=model)
    store = InMemoryUsageStore()
    service._usage_store = store

    await service.chat(ask("What is the return window?"))

    assert len(store.records) == 1
    record = store.records[0]
    assert record.model == "gpt-4.1-mini-2025-04-14"
    assert (record.usage.input_tokens, record.usage.cached_input_tokens) == (1000, 400)
    # (600 x 0.40 + 400 x 0.10 + 200 x 1.60) / 1M
    assert record.cost_usd == Decimal("0.000600")


# ------------------------------------------------------------------------ feedback


async def test_feedback_upserts_and_is_scoped_to_owner(service_factory) -> None:
    service = service_factory()
    store = InMemoryFeedbackStore()
    service._feedback_store = store
    turn = await service.chat(ask("What is the return window?"))
    assert turn.reply_message_id

    vote = FeedbackRequest(customer_id=CUSTOMER, message_id=turn.reply_message_id, rating="up")
    await service.feedback(turn.thread_id, vote)
    await service.feedback(turn.thread_id, vote.model_copy(update={"rating": "down"}))
    assert [r.rating for r in store.records.values()] == [-1]  # one vote, overwritten

    with pytest.raises(NotFoundError):
        await service.feedback(turn.thread_id, vote.model_copy(update={"customer_id": "cust_999"}))
    with pytest.raises(NotFoundError):
        await service.feedback(turn.thread_id, vote.model_copy(update={"message_id": "nope"}))


async def test_feedback_rejects_customer_messages(service_factory) -> None:
    service = service_factory()
    turn = await service.chat(ask("What is the return window?"))
    thread = await service.get_thread(turn.thread_id, CUSTOMER)
    customer_msg = next(m for m in thread.messages if m.role == "customer")
    with pytest.raises(NotFoundError):
        await service.feedback(
            turn.thread_id,
            FeedbackRequest(customer_id=CUSTOMER, message_id=customer_msg.message_id, rating="up"),
        )
