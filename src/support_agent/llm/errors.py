"""Classify LLM failures so the graph can degrade gracefully instead of 500-ing."""

from __future__ import annotations

import openai


def is_content_filter_error(exc: BaseException) -> bool:
    """Azure OpenAI rejects prompts/completions that trip its content filter with a 400."""
    return isinstance(exc, openai.BadRequestError) and (
        getattr(exc, "code", None) == "content_filter" or "content_filter" in str(exc)
    )
