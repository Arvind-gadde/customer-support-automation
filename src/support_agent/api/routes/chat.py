from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from support_agent.api.deps import get_agent_service
from support_agent.core.security import Principal, Role, require_roles
from support_agent.services.agent_service import AgentService
from support_agent.services.schemas import ChatRequest, TurnResult

router = APIRouter(prefix="/v1/chat", tags=["chat"])

Service = Annotated[AgentService, Depends(get_agent_service)]
Client = Annotated[Principal, Depends(require_roles(Role.CLIENT, Role.STAFF))]


@router.post("", response_model=TurnResult, summary="Send a customer message")
async def chat(body: ChatRequest, service: Service, _: Client) -> TurnResult:
    return await service.chat(body)


@router.post("/stream", summary="Send a customer message and stream the reply (SSE)")
async def chat_stream(body: ChatRequest, service: Service, _: Client) -> StreamingResponse:
    events = await service.open_stream(body)
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        # Stop Azure/NGINX proxies from buffering the stream.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
