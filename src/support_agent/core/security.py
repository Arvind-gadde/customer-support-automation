"""API-key authentication with two roles.

Topology assumption (see docs/adr/0004-auth-model.md): end users never call
this service directly. A BFF / API Management authenticates the user (Entra ID,
OTP, whatever) and calls us with a CLIENT key plus the verified customer_id.
Internal support staff use the agent console with a STAFF key.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class Role(StrEnum):
    CLIENT = "client"
    STAFF = "staff"


@dataclass(frozen=True, slots=True)
class Principal:
    role: Role
    key_fingerprint: str  # safe to log; the key itself never is


def _matches(candidate: str, keys: frozenset[str]) -> bool:
    # Compare against every key in constant time to avoid timing side channels.
    found = False
    for key in keys:
        found |= hmac.compare_digest(candidate.encode(), key.encode())
    return found


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


async def authenticate(
    request: Request, api_key: Annotated[str | None, Security(api_key_header)]
) -> Principal:
    settings = request.app.state.settings
    if api_key:
        if _matches(api_key, settings.staff_keys):
            return Principal(Role.STAFF, _fingerprint(api_key))
        if _matches(api_key, settings.client_keys):
            return Principal(Role.CLIENT, _fingerprint(api_key))
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key.",
        headers={"WWW-Authenticate": "ApiKey"},
    )


def require_roles(*roles: Role) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: Annotated[Principal, Security(authenticate)]) -> Principal:
        if principal.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role.")
        return principal

    return dependency
