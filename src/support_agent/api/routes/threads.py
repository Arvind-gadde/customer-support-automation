from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from support_agent.api.deps import get_agent_service
from support_agent.core.errors import ForbiddenError
from support_agent.core.security import Principal, Role, require_roles
from support_agent.services.agent_service import AgentService
from support_agent.services.schemas import (
    CustomerId,
    FeedbackRequest,
    FeedbackResult,
    ResumeRequest,
    ThreadView,
    TurnResult,
)

router = APIRouter(prefix="/v1/threads", tags=["threads"])
Service = Annotated[AgentService, Depends(get_agent_service)]


@router.get("/{thread_id}", response_model=ThreadView, summary="Get a conversation")
async def get_thread(
    thread_id: str,
    service: Service,
    principal: Annotated[Principal, Depends(require_roles(Role.CLIENT, Role.STAFF))],
    customer_id: Annotated[CustomerId | None, Query()] = None,
) -> ThreadView:
    # Clients must prove which customer they act for; staff can see any thread.
    if principal.role == Role.CLIENT and customer_id is None:
        raise ForbiddenError("customer_id is required.")
    return await service.get_thread(thread_id, customer_id)


@router.post(
    "/{thread_id}/resume",
    response_model=TurnResult,
    summary="Staff: approve/reject a pending action, or reply to a handed-off customer",
)
async def resume_thread(
    thread_id: str,
    body: ResumeRequest,
    service: Service,
    _: Annotated[Principal, Depends(require_roles(Role.STAFF))],
) -> TurnResult:
    return await service.resume(thread_id, body)


@router.post(
    "/{thread_id}/feedback",
    response_model=FeedbackResult,
    summary="Customer thumbs up/down on one assistant message (idempotent upsert)",
)
async def submit_feedback(
    thread_id: str,
    body: FeedbackRequest,
    service: Service,
    _: Annotated[Principal, Depends(require_roles(Role.CLIENT, Role.STAFF))],
) -> FeedbackResult:
    return await service.feedback(thread_id, body)
