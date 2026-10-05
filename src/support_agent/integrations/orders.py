"""Order service integration.

The agent depends on the OrderService Protocol, never on a concrete client.
Prod wires HttpOrderService; tests and local dev wire InMemoryOrderService.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

import httpx
from pydantic import BaseModel

from support_agent.integrations.http import request_json


class OrderItem(BaseModel):
    sku: str
    name: str
    quantity: int
    unit_price: Decimal


class Order(BaseModel):
    order_id: str
    customer_id: str
    status: str
    currency: str
    total: Decimal
    refunded_amount: Decimal = Decimal("0")
    items: list[OrderItem]
    created_at: datetime
    tracking_number: str | None = None

    @property
    def refundable_amount(self) -> Decimal:
        return self.total - self.refunded_amount


class Refund(BaseModel):
    refund_id: str
    order_id: str
    amount: Decimal
    status: str


class RefundError(ValueError):
    """Business-rule violation (not an outage) - surfaced to the LLM as a tool error."""


class OrderService(Protocol):
    async def get_order(self, order_id: str) -> Order | None: ...
    async def list_orders(self, customer_id: str, limit: int) -> list[Order]: ...
    async def refund(
        self, order_id: str, amount: Decimal, reason: str, idempotency_key: str
    ) -> Refund: ...


class HttpOrderService:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_order(self, order_id: str) -> Order | None:
        response = await request_json(
            self._client, "GET", f"/orders/{order_id}", service="order-service"
        )
        if response.status_code == 404:
            return None
        return Order.model_validate(response.json())

    async def list_orders(self, customer_id: str, limit: int) -> list[Order]:
        response = await request_json(
            self._client,
            "GET",
            "/orders",
            service="order-service",
            params={"customer_id": customer_id, "limit": limit},
        )
        return [Order.model_validate(o) for o in response.json()["items"]]

    async def refund(
        self, order_id: str, amount: Decimal, reason: str, idempotency_key: str
    ) -> Refund:
        # Idempotency-Key makes the POST safe to retry: the server dedupes it.
        response = await request_json(
            self._client,
            "POST",
            f"/orders/{order_id}/refunds",
            service="order-service",
            json={"amount": str(amount), "reason": reason},
            headers={"Idempotency-Key": idempotency_key},
        )
        if response.status_code == 404:
            raise RefundError("Order not found.")
        return Refund.model_validate(response.json())


class InMemoryOrderService:
    """Deterministic fake with seeded demo data. Used by tests, local dev and evals."""

    def __init__(self, orders: list[Order] | None = None) -> None:
        self._orders = {o.order_id: o for o in (orders or seed_orders())}
        self._refunds: dict[str, Refund] = {}

    async def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id.upper())

    async def list_orders(self, customer_id: str, limit: int) -> list[Order]:
        mine = [o for o in self._orders.values() if o.customer_id == customer_id]
        return sorted(mine, key=lambda o: o.created_at, reverse=True)[:limit]

    async def refund(
        self, order_id: str, amount: Decimal, reason: str, idempotency_key: str
    ) -> Refund:
        if idempotency_key in self._refunds:
            return self._refunds[idempotency_key]
        order = self._orders.get(order_id.upper())
        if order is None:
            raise RefundError("Order not found.")
        if amount <= 0 or amount > order.refundable_amount:
            raise RefundError(
                f"Refund amount must be between 0 and {order.refundable_amount} {order.currency}."
            )
        order.refunded_amount += amount
        refund = Refund(
            refund_id=f"RF-{len(self._refunds) + 1:05d}",
            order_id=order.order_id,
            amount=amount,
            status="processed",
        )
        self._refunds[idempotency_key] = refund
        return refund


def seed_orders() -> list[Order]:
    def _dt(day: int) -> datetime:
        return datetime(2026, 9, day, 10, 0, tzinfo=UTC)

    return [
        Order(
            order_id="ORD-1001",
            customer_id="cust_123",
            status="delivered",
            currency="INR",
            total=Decimal("2499.00"),
            items=[
                OrderItem(
                    sku="HP-01",
                    name="Wireless Headphones",
                    quantity=1,
                    unit_price=Decimal("2499.00"),
                )
            ],
            created_at=_dt(2),
            tracking_number="DLV123456789IN",
        ),
        Order(
            order_id="ORD-1002",
            customer_id="cust_123",
            status="shipped",
            currency="INR",
            total=Decimal("40.00"),
            items=[
                OrderItem(sku="CB-USB", name="USB-C Cable", quantity=1, unit_price=Decimal("40.00"))
            ],
            created_at=_dt(20),
            tracking_number="DLV987654321IN",
        ),
        Order(
            order_id="ORD-2001",
            customer_id="cust_999",
            status="processing",
            currency="INR",
            total=Decimal("15999.00"),
            items=[
                OrderItem(
                    sku="WT-07", name="Smart Watch", quantity=1, unit_price=Decimal("15999.00")
                )
            ],
            created_at=_dt(25),
        ),
    ]
