"""motivos_revisao na resposta da análise (GET /me/analysis e /scores/analyses/{id})."""
from datetime import datetime, timezone

import httpx
import respx

from app.analysis.repository import BusinessAnalysisRepository, em_analise_response, to_response
from app.analysis.service import motivos_revisao_response, run_analysis
from app.config import settings
from app.models import BusinessAnalysis


def _store(db, payload, event_id="evt-m"):
    row = BusinessAnalysisRepository(db).save_outcome(payload["customer_id"], run_analysis(payload), event_id)
    db.commit()
    return row


def test_manual_analysis_exposes_motivos(client, db_session, business_payload):
    business_payload["segmento"] = "outro"
    _store(db_session, business_payload)
    body = client.get("/scores/analyses/7").json()
    assert body["status"] == "ANALISE_MANUAL"
    assert body["motivos_revisao"][0]["codigo"] == "SEGMENTO_OUTRO"
    assert body["motivos_revisao"][0]["descricao"] == "Segmento 'outro' sempre vai para a análise manual"
    assert all(set(m) == {"codigo", "descricao"} for m in body["motivos_revisao"])


def test_divida_excessiva_description(client, db_session, business_payload):
    business_payload["respostas"]["dividas_total"] = 182000 * 5
    _store(db_session, business_payload)
    codigos = {m["codigo"]: m["descricao"] for m in client.get("/scores/analyses/7").json()["motivos_revisao"]}
    assert codigos["DIVIDA_EXCESSIVA"] == "Dívidas totais acima de 3x o faturamento mensal"


def test_aprovado_has_empty_list(client, db_session, business_payload):
    _store(db_session, business_payload)
    body = client.get("/scores/analyses/7").json()
    assert body["status"] == "APROVADO" and body["motivos_revisao"] == []


@respx.mock
def test_me_analysis_includes_motivos(client, db_session, business_payload):
    respx.get(f"{settings.onboarding_api_url}/me").mock(return_value=httpx.Response(
        200, json={"customer_id": 7, "onboarding_status": "ANALISE_MANUAL", "empresa": None}))
    business_payload["segmento"] = "outro"
    _store(db_session, business_payload)
    body = client.get("/me/analysis").json()
    assert [m["codigo"] for m in body["motivos_revisao"]][0] == "SEGMENTO_OUTRO"


def test_legacy_row_without_key_returns_empty():
    row = BusinessAnalysis(customer_id=1, version=1, status="ANALISE_MANUAL", score=500, faixa_risco="MEDIO",
                           gerado_por="regras", resultado={}, created_at=datetime.now(timezone.utc))
    assert to_response(row)["motivos_revisao"] == []


def test_helper_edge_cases():
    assert motivos_revisao_response("APROVADO", ["SEGMENTO_OUTRO"]) == []
    assert motivos_revisao_response("REPROVADO", None) == []
    assert motivos_revisao_response("ANALISE_MANUAL", ["DESCONHECIDO", 3]) == [
        {"codigo": "DESCONHECIDO", "descricao": "DESCONHECIDO"}
    ]
    assert em_analise_response(1)["motivos_revisao"] == []
