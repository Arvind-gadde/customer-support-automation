from __future__ import annotations

from fastapi import Request

from support_agent.services.agent_service import AgentService


def get_agent_service(request: Request) -> AgentService:
    service: AgentService = request.app.state.runtime.agent_service
    return service
