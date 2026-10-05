"""Shared resilient HTTP plumbing for downstream services."""

from __future__ import annotations

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from support_agent.core.errors import UpstreamError
from support_agent.core.logging import get_logger

logger = get_logger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    # Retry transport errors and 5xx/429. Never retry 4xx: those are our bugs.
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    service: str,
    attempts: int = 3,
    **kwargs: object,
) -> httpx.Response:
    """Send a request with jittered exponential backoff; raise UpstreamError when exhausted.

    Only use this for idempotent calls, or calls carrying an Idempotency-Key.
    """
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential_jitter(initial=0.2, max=3),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                response = await client.request(method, url, **kwargs)  # type: ignore[arg-type]
                if response.status_code != 404:
                    response.raise_for_status()
                return response
    except (httpx.HTTPError, httpx.TransportError) as exc:
        logger.error("upstream.failed", service=service, url=url, error=str(exc))
        raise UpstreamError(f"{service} is unavailable.") from exc
    raise AssertionError("unreachable")  # pragma: no cover
