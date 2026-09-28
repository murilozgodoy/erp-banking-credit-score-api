import httpx
import respx

from app.config import settings


BASE = settings.onboarding_api_url


@respx.mock
def test_create_evaluation_success(client, onboarding_response):
    respx.get(f"{BASE}/customers/1").mock(
        return_value=httpx.Response(200, json=onboarding_response)
    )
    r = client.post("/scores/evaluations", json={"customer_id": 1, "strategy": "weighted"})
    assert r.status_code == 201
    body = r.json()
    assert body["customer_id"] == 1
    assert body["strategy_usada"] == "weighted"
    assert body["decision"] is not None
    assert len(body["factors"]) > 0


@respx.mock
def test_create_evaluation_customer_not_found(client):
    respx.get(f"{BASE}/customers/99").mock(return_value=httpx.Response(404))
    r = client.post("/scores/evaluations", json={"customer_id": 99})
    assert r.status_code == 404


@respx.mock
def test_create_evaluation_unknown_strategy(client, onboarding_response):
    respx.get(f"{BASE}/customers/1").mock(
        return_value=httpx.Response(200, json=onboarding_response)
    )
    r = client.post("/scores/evaluations", json={"customer_id": 1, "strategy": "xyz"})
    assert r.status_code == 400


@respx.mock
def test_create_evaluation_upstream_error(client):
    respx.get(f"{BASE}/customers/1").mock(return_value=httpx.Response(500))
    r = client.post("/scores/evaluations", json={"customer_id": 1})
    assert r.status_code == 502


@respx.mock
def test_latest_returns_last_evaluation(client, onboarding_response):
    respx.get(f"{BASE}/customers/1").mock(
        return_value=httpx.Response(200, json=onboarding_response)
    )
    client.post("/scores/evaluations", json={"customer_id": 1, "strategy": "simple"})
    client.post("/scores/evaluations", json={"customer_id": 1, "strategy": "weighted"})

    r = client.get("/scores/customers/1/latest")
    assert r.status_code == 200
    assert r.json()["strategy_usada"] == "weighted"


def test_latest_not_found(client):
    r = client.get("/scores/customers/999/latest")
    assert r.status_code == 404


def test_invalid_payload(client):
    r = client.post("/scores/evaluations", json={"customer_id": 0})
    assert r.status_code == 422
