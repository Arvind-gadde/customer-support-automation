"""OpenTelemetry -> Azure Monitor (Application Insights).

Must be called AFTER configure_logging(): configure_logging() replaces root
handlers, and Azure Monitor appends its own OTel handler.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from opentelemetry import trace

from support_agent.core.config import Settings
from support_agent.core.logging import get_logger

logger = get_logger(__name__)
tracer = trace.get_tracer("support_agent")

P = ParamSpec("P")
R = TypeVar("R")


def configure_telemetry(settings: Settings) -> None:
    if settings.applicationinsights_connection_string is None:
        logger.info("telemetry.disabled", reason="no connection string")
        return

    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(
        connection_string=settings.applicationinsights_connection_string.get_secret_value(),
        logger_name="support_agent",
        enable_live_metrics=settings.is_production,
    )
    logger.info("telemetry.enabled", exporter="azure_monitor")


def traced_node(name: str) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Wrap a graph node in an OTel span + latency log.

    LangSmith-style tracing is great in dev; in prod the on-call team lives in
    App Insights, so every node shows up there as a span with its duration.
    """

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start = time.perf_counter()
            with tracer.start_as_current_span(f"agent.node.{name}") as span:
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(trace.Status(trace.StatusCode.ERROR))
                    raise
                finally:
                    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
                    span.set_attribute("agent.node.duration_ms", elapsed_ms)
                    logger.debug("agent.node.completed", node=name, duration_ms=elapsed_ms)

        return wrapper

    return decorator


def set_span_attributes(**attrs: Any) -> None:
    span = trace.get_current_span()
    for key, value in attrs.items():
        if value is not None:
            span.set_attribute(key, value)
