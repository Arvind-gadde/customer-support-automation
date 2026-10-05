"""Structured-output contracts for internal LLM calls.

Every field is required (no defaults) so the schemas are valid under OpenAI's
strict JSON-schema mode - the model cannot "forget" a field.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from support_agent.agent.state import Intent


class IntentDecision(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 confidence in the intent.")
    rationale: str = Field(description="One short sentence explaining the choice.")


class RelevanceGrade(BaseModel):
    relevant_doc_ids: list[str] = Field(
        description="IDs of documents that contain information needed to answer the question."
    )


class RewrittenQuery(BaseModel):
    query: str = Field(description="A standalone search query optimised for the knowledge base.")


class GroundednessVerdict(BaseModel):
    grounded: bool = Field(description="True only if every factual claim is supported.")
    unsupported_claims: list[str]
