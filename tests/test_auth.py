from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import auth
from app.main import app


@pytest.fixture
def auth_client(monkeypatch):
    monkeypatch.setattr(auth.settings, "auth0_domain", "test.auth0.com")
    monkeypatch.setattr(auth.settings, "auth0_audience", "https://erp-banking-api")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client = Mock()
    client.get_signing_key_from_jwt.return_value = SimpleNamespace(key=key.public_key())
    monkeypatch.setattr(auth, "jwks_client", lambda domain: client)
    with TestClient(app) as http:
        yield http, key, client


def token(key, **changes):
    claims = {"sub": "auth0|user", "iss": "https://test.auth0.com/",
              "aud": "https://erp-banking-api",
              "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    claims.update(changes)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


@pytest.mark.parametrize("header", [None, "Basic abc", "Bearer garbage"])
def test_missing_or_malformed_token(auth_client, header):
    http, _, _ = auth_client
    # Test a registered protected route, without executing its business logic.
    response = http.get(PROTECTED_PATH, headers={"Authorization": header} if header else {})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


PROTECTED_PATH = "/scores/customers/999999/latest"


@pytest.mark.parametrize("claims", [
    {"iss": "https://other.auth0.com/"}, {"aud": "other"},
    {"exp": datetime.now(timezone.utc) - timedelta(minutes=5)},
    {"exp": None}, {"iss": None}, {"aud": None}, {"sub": None},
])
def test_invalid_claims(auth_client, claims):
    http, key, _ = auth_client
    assert http.get(PROTECTED_PATH, headers={"Authorization": "Bearer " + token(key, **claims)}).status_code == 401


def test_signature_and_algorithm(auth_client):
    http, _, _ = auth_client
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert http.get(PROTECTED_PATH, headers={"Authorization": "Bearer " + token(other_key)}).status_code == 401
    bad = jwt.encode({"sub": "user"}, "a" * 32, algorithm="HS256")
    assert http.get(PROTECTED_PATH, headers={"Authorization": "Bearer " + bad}).status_code == 401


def test_valid_token(auth_client):
    http, key, _ = auth_client
    # Missing resource returns 404 only after authentication succeeds.
    assert http.get(PROTECTED_PATH, headers={"Authorization": "Bearer " + token(key)}).status_code == 404


def test_unavailable_signing_key(auth_client):
    http, key, client = auth_client
    client.get_signing_key_from_jwt.side_effect = jwt.PyJWKClientError("unknown kid")
    assert http.get(PROTECTED_PATH, headers={"Authorization": "Bearer " + token(key)}).status_code == 401


def test_missing_config(auth_client, monkeypatch):
    http, key, _ = auth_client
    monkeypatch.setattr(auth.settings, "auth0_audience", "")
    assert http.get(PROTECTED_PATH, headers={"Authorization": "Bearer " + token(key)}).status_code == 503


def test_health_and_cors(auth_client):
    http, _, _ = auth_client
    assert http.get("/health").status_code == 200
    response = http.options(PROTECTED_PATH, headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization, Content-Type"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    response = http.options(PROTECTED_PATH, headers={
        "Origin": "https://untrusted.example", "Access-Control-Request-Method": "GET"})
    assert response.status_code == 400


def test_jwks_url():
    assert auth.jwks_client("test.auth0.com").uri == "https://test.auth0.com/.well-known/jwks.json"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_documentation_requires_auth(auth_client, path):
    http, key, _ = auth_client
    assert http.get(path).status_code == 401
    assert http.get(path, headers={"Authorization": "Bearer " + token(key)}).status_code == 200
