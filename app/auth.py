"""Auth0 resource-server authentication; no OAuth login flow lives here."""
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import settings

bearer = HTTPBearer(auto_error=False)


@lru_cache
def jwks_client(domain: str) -> PyJWKClient:
    return PyJWKClient(f"https://{domain}/.well-known/jwks.json", timeout=5)


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    """Validate the access token and return it for downstream propagation."""
    if credentials is None:
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    if not settings.auth0_domain or not settings.auth0_audience:
        raise HTTPException(503, "Authentication is not configured")
    try:
        key = jwks_client(settings.auth0_domain).get_signing_key_from_jwt(credentials.credentials)
        jwt.decode(
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
    return credentials.credentials
