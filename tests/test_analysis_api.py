from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.analysis.repository import BusinessAnalysisRepository, to_response
from app.analysis.service import run_analysis
from app.auth import Principal, require_auth
from app.clients.onboarding_client import MeCache, MeInfo, OnboardingClient, OnboardingClientError
from app.config import settings
from app.main import app
from app.models import BusinessAnalysis

BASE = settings.onboarding_api_url
CONTRACT_KEYS = {
    "customer_id", "status", "score", "faixa_risco", "resumo", "gerado_por", "atualizado_em",
    "kpis", "insights", "oportunidades", "pontos_fortes", "riscos",
}


def store(db, payload, event_id="evt-1"):
    row = BusinessAnalysisRepository(db).save_outcome(payload["customer_id"], run_analysis(payload), event_id)
    db.commit()
    return row


def me(customer_id=7, status="APROVADO"):
    return httpx.Response(200, json={"customer_id": customer_id, "onboarding_status": status, "empresa": None})


# --- repositório -----------------------------------------------------------


def test_versions_increment_and_latest(db_session, business_payload):
    repo = BusinessAnalysisRepository(db_session)
    assert repo.latest(7) is None
    v1 = store(db_session, business_payload, "e1")
    v2 = store(db_session, business_payload, "e2")
    assert (v1.version, v2.version) == (1, 2)
    assert repo.latest(7).id == v2.id
    assert repo.exists_event("e1") and not repo.exists_event("e3")


def test_to_response_naive_datetime_and_defaults():
    row = BusinessAnalysis(
        customer_id=3, version=1, status="REPROVADO", score=None, faixa_risco=None, gerado_por="regras",
        resultado={}, created_at=datetime(2026, 11, 3, 12, 0),
    )
    body = to_response(row)
    assert set(body) == CONTRACT_KEYS
    assert body["atualizado_em"] == "2026-11-03T12:00:00+00:00"
    assert body["kpis"] == [] and body["resumo"] is None


# --- GET /scores/analyses/{customer_id} -----------------------------------


def test_staff_gets_latest_analysis(client, db_session, business_payload):
    store(db_session, business_payload, "e1")
    store(db_session, business_payload, "e2")
    r = client.get("/scores/analyses/7")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == CONTRACT_KEYS
    assert body["status"] == "APROVADO" and body["gerado_por"] == "regras"
    assert body["atualizado_em"]
    assert {k["id"] for k in body["kpis"]} >= {"receita_mensal", "cobertura_estoque_dias"}
    assert set(body["kpis"][0]) == {"id", "label", "valor", "unidade", "benchmark_segmento", "leitura"}
    assert set(body["insights"][0]) == {"area", "titulo", "texto", "severidade"}


def test_staff_analysis_not_found(client):
    assert client.get("/scores/analyses/999").status_code == 404


def test_staff_route_requires_read_scores(client):
    app.dependency_overrides[require_auth] = lambda: Principal("t", "auth0|x", frozenset({"read:self"}))
    assert client.get("/scores/analyses/7").status_code == 403


# --- GET /me/analysis -------------------------------------------------------


@respx.mock
def test_me_analysis_returns_latest_and_relays_token(client, db_session, business_payload):
    route = respx.get(f"{BASE}/me").mock(return_value=me())
    store(db_session, business_payload)
    r = client.get("/me/analysis")
    assert r.status_code == 200
    assert r.json()["customer_id"] == 7
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-token"
    # cache por sub: a segunda chamada não vai ao Onboarding
    client.get("/me/analysis")
    assert route.call_count == 1


@respx.mock
def test_me_analysis_without_customer(client):
    respx.get(f"{BASE}/me").mock(return_value=me(None, "NAO_INICIADO"))
    r = client.get("/me/analysis")
    assert r.status_code == 404
    assert r.json() == {"error": "ONBOARDING_NAO_INICIADO", "status": "NAO_INICIADO"}


@respx.mock
def test_me_analysis_em_analise(client):
    respx.get(f"{BASE}/me").mock(return_value=me(7, "EM_ANALISE"))
    r = client.get("/me/analysis")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == CONTRACT_KEYS
    assert body["customer_id"] == 7 and body["status"] == "EM_ANALISE"
    assert body["score"] is None and body["kpis"] == [] and body["oportunidades"] == []


@respx.mock
def test_me_analysis_refreshes_stale_status(client):
    route = respx.get(f"{BASE}/me").mock(side_effect=[me(7, "RASCUNHO"), me(7, "EM_ANALISE")])
    r = client.get("/me/analysis")
    assert r.status_code == 200 and r.json()["status"] == "EM_ANALISE"
    assert route.call_count == 2


@respx.mock
def test_me_analysis_draft_not_found(client):
    respx.get(f"{BASE}/me").mock(return_value=me(7, "RASCUNHO"))
    r = client.get("/me/analysis")
    assert r.status_code == 404
    assert r.json() == {"error": "ANALISE_NAO_ENCONTRADA", "status": "RASCUNHO"}


@respx.mock
@pytest.mark.parametrize("upstream,expected", [(403, 403), (401, 403), (500, 502)])
def test_me_analysis_onboarding_errors(client, upstream, expected):
    respx.get(f"{BASE}/me").mock(return_value=httpx.Response(upstream))
    assert client.get("/me/analysis").status_code == expected


def test_me_analysis_requires_read_self(client):
    app.dependency_overrides[require_auth] = lambda: Principal("t", "auth0|x", frozenset({"read:scores"}))
    assert client.get("/me/analysis").status_code == 403


# --- OnboardingClient.get_me e MeCache --------------------------------------


@respx.mock
def test_get_me_network_error():
    respx.get("http://onb.test/me").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(OnboardingClientError):
        OnboardingClient(base_url="http://onb.test").get_me()


@respx.mock
def test_get_me_maps_body():
    respx.get("http://onb.test/me").mock(return_value=httpx.Response(
        200, json={"customer_id": "5", "onboarding_status": "APROVADO", "empresa": {"cnpj": "1"}}
    ))
    assert OnboardingClient(base_url="http://onb.test").get_me() == MeInfo(5, "APROVADO", {"cnpj": "1"})


def test_me_cache_ttl_and_no_customer_not_cached():
    now = [0.0]
    cache = MeCache(ttl_seconds=300, clock=lambda: now[0])
    calls = []

    def fetch():
        calls.append(1)
        return MeInfo(9, "APROVADO")

    assert cache.get("s", fetch).customer_id == 9
    cache.get("s", fetch)
    assert len(calls) == 1
    now[0] = 301
    cache.get("s", fetch)
    assert len(calls) == 2
    cache.get("s", fetch, fresh=True)
    assert len(calls) == 3

    sem = []
    cache.get("t", lambda: sem.append(1) or MeInfo(None, "NAO_INICIADO"))
    cache.get("t", lambda: sem.append(1) or MeInfo(None, "NAO_INICIADO"))
    assert len(sem) == 2


def test_utc_timestamp_persisted(db_session, business_payload):
    row = store(db_session, business_payload)
    assert row.created_at.tzinfo is not None or row.created_at <= datetime.now(timezone.utc).replace(tzinfo=None)
