"""Business policies that gate agent actions. Pure functions => trivially testable."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SENSITIVE_TOOLS = frozenset({"issue_refund"})


def requires_approval(tool_call: Mapping[str, Any], auto_approve_limit: float) -> bool:
    """Refunds above the auto-approve limit need a human. Unparseable amounts => approval."""
    if tool_call["name"] not in SENSITIVE_TOOLS:
        return False
    try:
        amount = float(tool_call["args"].get("amount"))
    except (TypeError, ValueError):
        return True
    return amount > auto_approve_limit
