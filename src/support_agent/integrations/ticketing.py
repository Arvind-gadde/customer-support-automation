"""Ticketing integration (Zendesk / Freshdesk / ServiceNow in real life)."""

from __future__ import annotations

from typing import Protocol

import httpx
from pydantic import BaseModel

from support_agent.integrations.http import request_json


class Ticket(BaseModel):
    ticket_id: str
    customer_id: str
    reason: str
    status: str = "open"


class TicketingClient(Protocol):
    async def create_ticket(
        self, *, customer_id: str, reason: str, transcript: str, idempotency_key: str
    ) -> Ticket: ...


class HttpTicketingClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def create_ticket(
        self, *, customer_id: str, reason: str, transcript: str, idempotency_key: str
    ) -> Ticket:
        response = await request_json(
            self._client,
            "POST",
            "/tickets",
            service="ticketing",
            json={"customer_id": customer_id, "reason": reason, "transcript": transcript},
            headers={"Idempotency-Key": idempotency_key},
        )
        return Ticket.model_validate(response.json())


class InMemoryTicketingClient:
    def __init__(self) -> None:
        self.tickets: dict[str, Ticket] = {}

    async def create_ticket(
        self, *, customer_id: str, reason: str, transcript: str, idempotency_key: str
    ) -> Ticket:
        if idempotency_key not in self.tickets:
            self.tickets[idempotency_key] = Ticket(
                ticket_id=f"TCK-{len(self.tickets) + 1:05d}",
                customer_id=customer_id,
                reason=reason,
            )
        return self.tickets[idempotency_key]
