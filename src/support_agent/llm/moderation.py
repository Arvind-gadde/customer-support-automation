"""Content moderation (OpenAI moderation endpoint) behind a Protocol.

Fail-open by design: if moderation itself is down, we log loudly and let the
message through rather than take the whole support channel offline. The other
layers (regex guards, tool scoping, groundedness) still apply. Flip
`fail_closed=True` for a higher-risk deployment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import openai

from support_agent.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ModerationResult:
    flagged: bool
    categories: list[str] = field(default_factory=list)


NOT_FLAGGED = ModerationResult(flagged=False)


class Moderator(Protocol):
    async def check(self, text: str) -> ModerationResult: ...


class NoopModerator:
    async def check(self, text: str) -> ModerationResult:
        return NOT_FLAGGED


class OpenAIModerator:
    def __init__(
        self, client: openai.AsyncOpenAI, model: str, *, fail_closed: bool = False
    ) -> None:
        self._client = client
        self._model = model
        self._fail_closed = fail_closed

    async def check(self, text: str) -> ModerationResult:
        if not text.strip():
            return NOT_FLAGGED
        try:
            response = await self._client.moderations.create(model=self._model, input=text)
        except openai.OpenAIError as exc:
            logger.error("moderation.unavailable", error_type=type(exc).__name__)
            return (
                ModerationResult(True, ["moderation_unavailable"])
                if self._fail_closed
                else NOT_FLAGGED
            )
        result = response.results[0]
        if not result.flagged:
            return NOT_FLAGGED
        categories = [name for name, hit in result.categories.model_dump().items() if hit]
        return ModerationResult(flagged=True, categories=categories)
