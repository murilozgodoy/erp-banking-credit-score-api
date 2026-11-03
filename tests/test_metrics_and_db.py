from unittest.mock import MagicMock

import httpx
import respx
from fastapi.testclient import TestClient

from app import database, main
from app.auth import Principal, require_auth
from app.config import settings
from app.database import init_db, schema_from_url
from app.metrics import SCORE_EVALUATIONS


def test_metrics_endpoint_is_public(client):
    client.get("/health")
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text
    assert "score_evaluations_total" in r.text
    assert "events_consumed_total" in r.text


@respx.mock
def test_evaluation_increments_business_metric(client, onboarding_response):
    respx.get(f"{settings.onboarding_api_url}/customers/1").mock(
        return_value=httpx.Response(200, json=onboarding_response)
    )
    r = client.post("/scores/evaluations", json={"customer_id": 1, "strategy": "simple"})
    faixa = r.json()["faixa_risco"]
    assert SCORE_EVALUATIONS.labels("simple", faixa)._value.get() >= 1


def test_schema_from_url():
    url = "postgresql+psycopg://erp:erp@postgres:5432/erp?options=-csearch_path%3Dcredit_score"
    assert schema_from_url(url) == "credit_score"
    assert schema_from_url("postgresql+psycopg://u:p@h/db?options=-csearch_path=x") == "x"
    assert schema_from_url("postgresql+psycopg://u:p@h/db") is None
    assert schema_from_url("sqlite:///./x.db") is None


def test_init_db_creates_schema_on_postgres(monkeypatch):
    bind = MagicMock()
    bind.url.render_as_string.return_value = (
        "postgresql+psycopg://erp:erp@pg/erp?options=-csearch_path%3Dcredit_score"
    )
    conn = bind.begin.return_value.__enter__.return_value
    create_all = MagicMock()
    monkeypatch.setattr(database.Base.metadata, "create_all", create_all)
    init_db(bind)
    assert 'CREATE SCHEMA IF NOT EXISTS "credit_score"' in str(conn.execute.call_args.args[0])
    create_all.assert_called_once_with(bind=bind)


def test_docs_use_root_path(monkeypatch):
    monkeypatch.setattr(main.settings, "root_path", "/score")
    monkeypatch.setattr(main, "Instrumentator", MagicMock())
    app = main.create_app()
    app.dependency_overrides[require_auth] = lambda: Principal("t", "s", frozenset())
    with TestClient(app) as c:
        assert "/score/openapi.json" in c.get("/docs").text
        assert "/score/openapi.json" in c.get("/redoc").text
        assert c.get("/openapi.json").json()["servers"] == [{"url": "/score"}]


def test_metric_series_are_initialized_with_zero(client):
    from app.metrics import init_metrics

    init_metrics()  # idempotente
    body = client.get("/metrics").text
    assert 'events_consumed_total{result="duplicate",routing_key="credit.decided"} ' in body
    assert 'events_consumed_total{result="invalid",routing_key="customer.anamnesis_updated"} ' in body
    assert 'score_evaluations_total{faixa_risco="MUITO_ALTO",strategy="weighted"} ' in body
