"""Classify LLM failures so the graph can degrade gracefully instead of 500-ing."""

from __future__ import annotations

import openai

# Error codes providers use when they refuse a prompt on safety grounds.
# OpenAI's moderation is mostly a separate call (llm/moderation.py), but
# OpenAI-compatible gateways and Azure-hosted models return these as 400s.
_SAFETY_CODES = frozenset({"content_filter", "content_policy_violation"})


def is_content_filter_error(exc: BaseException) -> bool:
    if not isinstance(exc, openai.BadRequestError):
        return False
    code = getattr(exc, "code", None)
    return code in _SAFETY_CODES or any(c in str(exc) for c in _SAFETY_CODES)
