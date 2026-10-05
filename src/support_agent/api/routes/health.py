"""Liveness vs readiness - Container Apps probes hit these.

/live  : process is up. Failing => container restart. Never checks dependencies
         (a DB blip must not restart every replica at once).
/ready : dependencies reachable. Failing => replica removed from load balancing.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request, Response, status

router = APIRouter(prefix="/health", tags=["health"], include_in_schema=False)


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, object]:
    checks = request.app.state.runtime.health_checks
    results: dict[str, str] = {}
    for name, check in checks.items():
        try:
            await asyncio.wait_for(check(), timeout=2.0)
            results[name] = "ok"
        except Exception as exc:
            results[name] = f"error: {type(exc).__name__}"
    healthy = all(v == "ok" for v in results.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "degraded", "checks": results}
