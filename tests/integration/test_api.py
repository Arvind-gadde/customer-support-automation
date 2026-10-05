"""HTTP contract tests: auth, roles, error envelope, SSE."""

from __future__ import annotations

from contextlib import AsyncExitStack

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from support_agent.agent.graph import build_graph
from support_agent.bootstrap import Runtime
from support_agent.main import create_app
from support_agent.services.agent_service import AgentService
from tests.fakes import make_deps

CLIENT = {"X-API-Key": "client-key"}
STAFF = {"X-API-Key": "staff-key"}


@pytest.fixture
async def client(settings, retriever):  # type: ignore[no-untyped-def]
    async def runtime_factory(s, stack: AsyncExitStack) -> Runtime:
        graph = build_graph(make_deps(s, retriever=retriever), InMemorySaver())
        return Runtime(agent_service=AgentService(graph, s))

    app = create_app(settings, runtime_factory=runtime_factory)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_health(client) -> None:
    assert (await client.get("/health/live")).status_code == 200
    assert (await client.get("/health/ready")).json()["status"] == "ok"


async def test_requires_api_key(client) -> None:
    response = await client.post("/v1/chat", json={"customer_id": "c1", "message": "hi"})
    assert response.status_code == 401


async def test_chat_roundtrip_and_request_id(client) -> None:
    response = await client.post(
        "/v1/chat",
        json={"customer_id": "cust_123", "message": "hi"},
        headers={**CLIENT, "X-Request-ID": "abc123"},
    )
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "abc123"
    assert response.json()["status"] == "completed"


async def test_validation_rejects_bad_customer_id(client) -> None:
    response = await client.post(
        "/v1/chat", json={"customer_id": "../etc", "message": "hi"}, headers=CLIENT
    )
    assert response.status_code == 422


async def test_clients_cannot_resume_threads(client) -> None:
    started = await client.post(
        "/v1/chat", json={"customer_id": "cust_123", "message": "human please"}, headers=CLIENT
    )
    thread_id = started.json()["thread_id"]
    body = {"decision": {"type": "handoff", "agent_name": "x", "message": "y"}}

    forbidden = await client.post(f"/v1/threads/{thread_id}/resume", json=body, headers=CLIENT)
    assert forbidden.status_code == 403

    ok = await client.post(f"/v1/threads/{thread_id}/resume", json=body, headers=STAFF)
    assert ok.status_code == 200
    assert ok.json()["reply"] == "y"


async def test_other_customers_thread_is_404(client) -> None:
    started = await client.post(
        "/v1/chat", json={"customer_id": "cust_123", "message": "hi"}, headers=CLIENT
    )
    thread_id = started.json()["thread_id"]
    response = await client.get(
        f"/v1/threads/{thread_id}", params={"customer_id": "cust_999"}, headers=CLIENT
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_stream_emits_start_and_final(client) -> None:
    async with client.stream(
        "POST", "/v1/chat/stream", json={"customer_id": "cust_123", "message": "hi"}, headers=CLIENT
    ) as response:
        assert response.status_code == 200
        body = "".join([chunk async for chunk in response.aiter_text()])
    assert "event: start" in body
    assert "event: final" in body


async def test_stream_validation_error_is_a_real_status_code(client) -> None:
    started = await client.post(
        "/v1/chat", json={"customer_id": "cust_123", "message": "hi"}, headers=CLIENT
    )
    response = await client.post(
        "/v1/chat/stream",
        json={"customer_id": "cust_999", "message": "hi", "thread_id": started.json()["thread_id"]},
        headers=CLIENT,
    )
    assert response.status_code == 403
