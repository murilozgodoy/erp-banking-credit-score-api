"""Auth0 resource-server authentication; no OAuth login flow lives here."""
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import settings

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """Authenticated caller: raw token (for propagation), subject and Auth0 permissions."""

    token: str
    subject: str
    permissions: frozenset[str]


@lru_cache
def jwks_client(domain: str) -> PyJWKClient:
    return PyJWKClient(f"https://{domain}/.well-known/jwks.json", timeout=5)


def _permissions(claims: dict) -> frozenset[str]:
    """Auth0 RBAC sends `permissions`; machine-to-machine tokens carry them in `scope`."""
    granted = set(claims.get("permissions") or [])
    scope = claims.get("scope")
    if isinstance(scope, str):
        granted.update(scope.split())
    return frozenset(granted)


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Principal:
    """Validate the access token and return the authenticated principal."""
    if credentials is None:
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    if not settings.auth0_domain or not settings.auth0_audience:
        raise HTTPException(503, "Authentication is not configured")
    try:
        key = jwks_client(settings.auth0_domain).get_signing_key_from_jwt(credentials.credentials)
        claims = jwt.decode(
            credentials.credentials,
            key.key,
            algorithms=["RS256"],
            issuer=f"https://{settings.auth0_domain}/",
            audience=settings.auth0_audience,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            401, "Invalid access token", headers={"WWW-Authenticate": "Bearer"}
        ) from exc
    return Principal(
        token=credentials.credentials,
        subject=claims["sub"],
        permissions=_permissions(claims),
    )


def require_permission(permission: str):
    """Dependency factory: 403 unless the token grants `permission`."""

    def dependency(principal: Principal = Depends(require_auth)) -> Principal:
        if permission not in principal.permissions:
            raise HTTPException(403, f"Missing permission: {permission}")
        return principal

    return dependency
