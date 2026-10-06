"""HTTP-level tests for the reranker and moderation clients (no network)."""

from __future__ import annotations

from decimal import Decimal

import httpx
import openai
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from support_agent.core.config import Settings
from support_agent.llm.moderation import OpenAIModerator
from support_agent.llm.usage import ModelUsage, UsageTracker, cost_usd, price_for
from support_agent.rag.reranker import CohereReranker

DOCS = [
    {"id": str(i), "source": f"{i}.md", "title": f"T{i}", "content": f"C{i}", "score": 0.1}
    for i in range(3)
]


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x")


async def test_cohere_reranker_reorders_and_rescores() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/rerank"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 2, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.4},
                ]
            },
        )

    ranked = await CohereReranker(_client(handler), "rerank-v3.5").rerank("q", DOCS, 2)
    assert [d["id"] for d in ranked] == ["2", "0"]
    assert ranked[0]["score"] == 0.9


async def test_cohere_outage_degrades_to_retrieval_order() -> None:
    ranked = await CohereReranker(
        _client(lambda r: httpx.Response(400, json={"message": "bad"})), "m"
    ).rerank("q", DOCS, 2)
    assert [d["id"] for d in ranked] == ["0", "1"]


def _openai(handler) -> openai.AsyncOpenAI:  # type: ignore[no-untyped-def]
    return openai.AsyncOpenAI(api_key="sk-test", max_retries=0, http_client=_client(handler))


async def test_moderation_fails_open_by_default_and_closed_when_configured() -> None:
    down = _openai(lambda r: httpx.Response(500, json={"error": {"message": "down"}}))
    assert not (await OpenAIModerator(down, "omni-moderation-latest").check("hi")).flagged
    closed = await OpenAIModerator(down, "omni-moderation-latest", fail_closed=True).check("hi")
    assert closed.flagged


async def test_moderation_reports_flagged_categories() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "modr-1",
                "model": "omni-moderation-latest",
                "results": [
                    {
                        "flagged": True,
                        "categories": {"harassment": True, "violence": False},
                        "category_scores": {"harassment": 0.97, "violence": 0.01},
                    }
                ],
            },
        )

    result = await OpenAIModerator(_openai(handler), "omni-moderation-latest").check("x")
    assert result.flagged
    assert "harassment" in result.categories
    assert "violence" not in result.categories


def test_usage_tracker_sums_by_model() -> None:
    tracker = UsageTracker()
    message = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        response_metadata={"model_name": "gpt-4.1-mini-2025-04-14"},
    )
    result = LLMResult(generations=[[ChatGeneration(message=message)]])
    tracker.on_llm_end(result)
    tracker.on_llm_end(result)
    usage = tracker.by_model["gpt-4.1-mini-2025-04-14"]
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (2, 200, 20)


def test_pricing_uses_longest_prefix_and_never_guesses() -> None:
    pricing = Settings(_env_file=None, environment="test").model_pricing
    assert price_for("gpt-4.1-mini-2025-04-14", pricing) == pricing["gpt-4.1-mini"]
    assert price_for("gpt-4.1-2025-04-14", pricing) == pricing["gpt-4.1"]
    assert cost_usd("some-new-model", ModelUsage(1, 10, 0, 10), pricing) is None
    assert cost_usd("gpt-4.1", ModelUsage(1, 1_000_000, 0, 0), pricing) == Decimal("2.000000")


def test_missing_openai_key_fails_at_boot_outside_tests() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(_env_file=None, environment="local", openai_api_key=None)
    with pytest.raises(ValidationError, match="COHERE_API_KEY"):
        Settings(_env_file=None, environment="test", reranker_backend="cohere")
