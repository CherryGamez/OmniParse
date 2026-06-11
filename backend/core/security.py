"""
Mock OIDC / JWT validation and Role-Based Access Control (RBAC).

For local demos we mint and validate HS256 JWTs with a shared secret. In a real
deployment `decode_token` would instead validate an RS256 token against the
identity provider's JWKS endpoint (the function signature would stay the same),
so the rest of the application is insulated from the change.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from core.config import get_settings

logger = logging.getLogger("security")
settings = get_settings()

# auto_error=False so we can raise our own RFC 7807 friendly error.
_bearer = HTTPBearer(auto_error=False)


class Principal(BaseModel):
    """The authenticated caller derived from a validated token."""

    sub: str
    roles: list[str] = Field(default_factory=list)


def create_mock_token(sub: str, roles: list[str], expires_minutes: int = 60) -> str:
    """Mint a signed mock OIDC token for local testing / the demo UI."""
    now = datetime.now(timezone.utc)
    claims = {
        "sub": sub,
        "roles": roles,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Validate signature, audience and expiry, returning the claim set."""
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        audience=settings.jwt_audience,
    )


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """FastAPI dependency that resolves the caller from the bearer token."""
    # Local escape hatch only.
    if settings.auth_disabled:
        return Principal(sub="anonymous", roles=["admin", "extractor"])

    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization bearer token.",
        )

    try:
        claims = decode_token(credentials.credentials)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
        ) from exc
    else:
        # `claims` is guaranteed bound here (the except branch always raises).
        return Principal(sub=str(claims.get("sub", "unknown")), roles=list(claims.get("roles", [])))


def require_roles(*required_roles: str):
    """Return a dependency enforcing that the caller holds at least one role."""

    async def _checker(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not set(required_roles).intersection(principal.roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Requires one of roles: {list(required_roles)}.",
            )
        return principal

    return _checker
