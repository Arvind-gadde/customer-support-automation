"""Request/response contracts of the agent service (shared with the API layer)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

CustomerId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
ThreadId = Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")]


class TurnStatus(StrEnum):
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"  # a staff member must approve an action
    AWAITING_HUMAN = "awaiting_human"  # handed off; a staff member will reply


class ChatRequest(BaseModel):
    customer_id: CustomerId
    message: str = Field(min_length=1, max_length=8000)
    thread_id: ThreadId | None = None


class PendingAction(BaseModel):
    type: Literal["approval", "handoff"]
    payload: dict[str, Any]


class TurnResult(BaseModel):
    thread_id: str
    status: TurnStatus
    reply: str | None
    intent: str | None = None
    ticket_id: str | None = None
    pending: PendingAction | None = None


class ApprovalDecision(BaseModel):
    type: Literal["approval"] = "approval"
    approved: bool
    reviewer: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=1000)


class HumanReply(BaseModel):
    type: Literal["handoff"] = "handoff"
    agent_name: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=8000)


class ResumeRequest(BaseModel):
    decision: Annotated[ApprovalDecision | HumanReply, Field(discriminator="type")]


class MessageView(BaseModel):
    role: Literal["customer", "assistant", "human_agent"]
    content: str


class ThreadView(BaseModel):
    thread_id: str
    customer_id: str
    status: TurnStatus
    messages: list[MessageView]
    pending: PendingAction | None = None
