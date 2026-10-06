"""Token usage and cost tracking.

A callback handler attached per turn sees every chat-model call inside the
graph (router, grader, answerer, judges, tool agent) and sums tokens by model.
Embedding calls don't emit LLM callbacks, so they're not counted here; at
$0.02 / 1M tokens they're noise next to chat costs, and the doc says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from support_agent.core.config import ModelPrice

_PER_MILLION = Decimal(1_000_000)


@dataclass
class ModelUsage:
    calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0


class UsageTracker(BaseCallbackHandler):
    """Collects usage for ONE turn. Create a fresh instance per invocation."""

    run_inline = True  # we're called from async code; don't hop to a thread

    def __init__(self) -> None:
        self.by_model: dict[str, ModelUsage] = {}

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None)
                if not usage:
                    continue
                metadata = getattr(message, "response_metadata", {}) or {}
                model = (
                    metadata.get("model_name")
                    or (response.llm_output or {}).get("model_name")
                    or "unknown"
                )
                details = usage.get("input_token_details") or {}
                bucket = self.by_model.setdefault(model, ModelUsage())
                bucket.calls += 1
                bucket.input_tokens += usage.get("input_tokens", 0)
                bucket.cached_input_tokens += details.get("cache_read", 0) or 0
                bucket.output_tokens += usage.get("output_tokens", 0)

    @property
    def total_tokens(self) -> int:
        return sum(u.input_tokens + u.output_tokens for u in self.by_model.values())


def price_for(model: str, pricing: dict[str, ModelPrice]) -> ModelPrice | None:
    """Longest-prefix match: 'gpt-4.1-mini-2025-04-14' -> 'gpt-4.1-mini', not 'gpt-4.1'."""
    matches = [name for name in pricing if model.startswith(name)]
    return pricing[max(matches, key=len)] if matches else None


def cost_usd(model: str, usage: ModelUsage, pricing: dict[str, ModelPrice]) -> Decimal | None:
    price = price_for(model, pricing)
    if price is None:
        return None  # unknown model: report tokens, never invent a price
    uncached = usage.input_tokens - usage.cached_input_tokens
    total = (
        Decimal(uncached) * Decimal(str(price.input))
        + Decimal(usage.cached_input_tokens) * Decimal(str(price.cached_input))
        + Decimal(usage.output_tokens) * Decimal(str(price.output))
    )
    return (total / _PER_MILLION).quantize(Decimal("0.000001"))
