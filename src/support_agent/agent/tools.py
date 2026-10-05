"""Tools exposed to the account agent.

SECURITY: `customer_id` is injected from graph state (InjectedState), so it is
NOT part of the schema the LLM sees. The model cannot be prompt-injected into
querying another customer's orders - that's an authorization boundary enforced
in code, not in the prompt.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState

from support_agent.integrations.orders import Order, OrderService, RefundError

NOT_FOUND = {"error": "order_not_found", "message": "No order with that ID on this account."}


def _public_view(order: Order) -> dict[str, Any]:
    return order.model_dump(mode="json", exclude={"customer_id"}) | {
        "refundable_amount": str(order.refundable_amount)
    }


def build_tools(orders: OrderService) -> list[BaseTool]:
    async def _owned_order(order_id: str, customer_id: str) -> Order | None:
        order = await orders.get_order(order_id.strip().upper())
        # Same response for "doesn't exist" and "not yours": don't leak existence.
        return order if order and order.customer_id == customer_id else None

    @tool(parse_docstring=True)
    async def lookup_order(
        order_id: str, customer_id: Annotated[str, InjectedState("customer_id")]
    ) -> dict[str, Any]:
        """Get status, items, totals and tracking for one of the customer's orders.

        Args:
            order_id: Order identifier, e.g. "ORD-1001".
        """
        order = await _owned_order(order_id, customer_id)
        return _public_view(order) if order else NOT_FOUND

    @tool(parse_docstring=True)
    async def list_recent_orders(
        customer_id: Annotated[str, InjectedState("customer_id")], limit: int = 5
    ) -> list[dict[str, Any]]:
        """List the customer's most recent orders, newest first.

        Args:
            limit: Maximum number of orders to return (1-10).
        """
        recent = await orders.list_orders(customer_id, max(1, min(limit, 10)))
        return [_public_view(o) for o in recent]

    @tool(parse_docstring=True)
    async def issue_refund(
        order_id: str,
        amount: float,
        reason: str,
        customer_id: Annotated[str, InjectedState("customer_id")],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> dict[str, Any]:
        """Refund part or all of one of the customer's orders.

        Args:
            order_id: Order identifier, e.g. "ORD-1001".
            amount: Amount to refund in the order's currency.
            reason: Short customer-provided reason for the refund.
        """
        order = await _owned_order(order_id, customer_id)
        if order is None:
            return NOT_FOUND
        try:
            value = Decimal(str(amount)).quantize(Decimal("0.01"))
        except InvalidOperation:
            return {"error": "invalid_amount"}
        try:
            # tool_call_id is stable across graph replays => natural idempotency key.
            refund = await orders.refund(
                order.order_id, value, reason, idempotency_key=tool_call_id
            )
        except RefundError as exc:
            return {"error": "refund_rejected", "message": str(exc)}
        return refund.model_dump(mode="json")

    return [lookup_order, list_recent_orders, issue_refund]
