import copy

import pytest

from app.analysis.service import (
    CONFIANCA_MINIMA,
    _normalize_verdict,
    descricao_motivo,
    run_analysis,
)


class FakeLlm:
    def __init__(self, verdict=None, error=None):
        self.verdict = verdict
        self.error = error
        self.calls = []

    def analyze(self, payload, rules_result):
        self.calls.append((payload, rules_result))
        if self.error:
            raise self.error
        return self.verdict


def verdict(**overrides):
    v = {
        "enquadramento": "PADRAO",
        "confianca": 0.9,
        "resumo": "Varejo consolidado com boa margem.",
        "pontos_fortes": ["Marca local forte", "Baixo endividamento em relação à receita"],
        "riscos": ["Concorrência de marketplaces"],
        "insights": [
            {"area": "vendas", "titulo": "Canais", "texto": "E-commerce cresce.", "severidade": "oportunidade"},
            {"area": "xpto", "titulo": "", "texto": "Área desconhecida vira geral.", "severidade": "urgente"},
            {"area": "vendas", "texto": ""},
            "não é dict",
        ],
    }
    v.update(overrides)
    return v


def test_rules_only_approved(business_payload):
    out = run_analysis(business_payload)
    assert out.status == "APROVADO"
    assert out.gerado_por == "regras"
    assert out.motivos_revisao == []
    assert set(out.body) == {
        "customer_id", "status", "score", "faixa_risco", "resumo", "gerado_por", "atualizado_em",
        "kpis", "insights", "oportunidades", "pontos_fortes", "riscos", "motivos_revisao",
    }
    assert out.body["customer_id"] == 7
    assert out.body["score"] == out.score


def test_llm_verdict_is_merged(business_payload):
    llm = FakeLlm(verdict())
    rules_only = run_analysis(business_payload)
    out = run_analysis(business_payload, llm=llm)
    assert llm.calls and llm.calls[0][1]["score"] == rules_only.score
    assert out.gerado_por == "llm+regras"
    assert out.status == "APROVADO"
    assert out.body["resumo"] == "Varejo consolidado com boa margem."
    # dedupe: "Baixo endividamento..." já vinha das regras
    assert out.body["pontos_fortes"].count("Baixo endividamento em relação à receita") == 1
    assert "Marca local forte" in out.body["pontos_fortes"]
    assert "Concorrência de marketplaces" in out.body["riscos"]
    extras = out.body["insights"][len(rules_only.body["insights"]):]
    assert extras == [
        {"area": "vendas", "titulo": "Canais", "texto": "E-commerce cresce.", "severidade": "oportunidade"},
        {"area": "geral", "titulo": "Insight", "texto": "Área desconhecida vira geral.", "severidade": "info"},
    ]


def test_llm_duplicate_insight_not_appended(business_payload):
    rules_only = run_analysis(business_payload)
    first = rules_only.body["insights"][0]
    out = run_analysis(business_payload, llm=FakeLlm(verdict(insights=[first], resumo=None)))
    assert len(out.body["insights"]) == len(rules_only.body["insights"])
    assert out.body["resumo"] == rules_only.body["resumo"]


def test_llm_especifico_goes_manual(business_payload):
    out = run_analysis(business_payload, llm=FakeLlm(verdict(enquadramento="especifico")))
    assert out.status == "ANALISE_MANUAL"
    assert out.motivos_revisao == ["LLM_ESPECIFICO"]


def test_llm_low_confidence_goes_manual(business_payload):
    out = run_analysis(business_payload, llm=FakeLlm(verdict(confianca=CONFIANCA_MINIMA - 0.01)))
    assert out.status == "ANALISE_MANUAL"
    assert out.motivos_revisao == ["LLM_BAIXA_CONFIANCA"]


def test_llm_confidence_at_threshold_ok(business_payload):
    assert run_analysis(business_payload, llm=FakeLlm(verdict(confianca=0.6))).status == "APROVADO"


def test_segment_outro_goes_manual(business_payload):
    p = copy.deepcopy(business_payload)
    p["segmento"] = "outro"
    out = run_analysis(p, llm=FakeLlm(verdict()))
    assert out.status == "ANALISE_MANUAL"
    assert out.motivos_revisao[0] == "SEGMENTO_OUTRO"


def test_critical_rule_goes_manual(business_payload):
    p = copy.deepcopy(business_payload)
    p["respostas"]["dividas_total"] = 1_000_000
    out = run_analysis(p)
    assert out.status == "ANALISE_MANUAL"
    assert "DIVIDA_EXCESSIVA" in out.motivos_revisao


@pytest.mark.parametrize("llm", [FakeLlm(None), FakeLlm(error=TimeoutError("20s")), FakeLlm({"x": 1})])
def test_llm_failure_falls_back_to_rules(business_payload, llm):
    out = run_analysis(business_payload, llm=llm)
    assert out.gerado_por == "regras"
    assert out.status == "APROVADO"


@pytest.mark.parametrize("raw", [
    None, [], {"enquadramento": "OUTRO", "confianca": 1},
    {"enquadramento": "PADRAO", "confianca": "alta"}, {"enquadramento": "PADRAO", "confianca": float("nan")},
])
def test_normalize_verdict_rejects_invalid(raw):
    assert _normalize_verdict(raw) is None


def test_normalize_verdict_clamps_and_defaults():
    v = _normalize_verdict({"enquadramento": "padrao", "confianca": 7, "pontos_fortes": "x", "insights": "y"})
    assert v == {
        "enquadramento": "PADRAO", "confianca": 1.0, "resumo": None,
        "pontos_fortes": [], "riscos": [], "insights": [],
    }


def test_descricao_motivo():
    assert "3x" in descricao_motivo("DIVIDA_EXCESSIVA")
    assert "outro" in descricao_motivo("SEGMENTO_OUTRO")
    assert descricao_motivo("X") == "X"


def _metric(counter, **labels):
    return counter.labels(**labels)._value.get()


def test_metrics_business_analyses_and_llm_disabled(business_payload):
    from app.metrics import BUSINESS_ANALYSES, LLM_REQUESTS

    before = _metric(BUSINESS_ANALYSES, status="APROVADO", gerado_por="regras", segmento="varejo")
    disabled = _metric(LLM_REQUESTS, result="disabled")
    run_analysis(business_payload)
    assert _metric(BUSINESS_ANALYSES, status="APROVADO", gerado_por="regras", segmento="varejo") == before + 1
    assert _metric(LLM_REQUESTS, result="disabled") == disabled + 1

    llm_before = _metric(BUSINESS_ANALYSES, status="APROVADO", gerado_por="llm+regras", segmento="varejo")
    run_analysis(business_payload, llm=FakeLlm(verdict()))
    assert _metric(LLM_REQUESTS, result="disabled") == disabled + 1  # com LLM não conta disabled
    assert _metric(BUSINESS_ANALYSES, status="APROVADO", gerado_por="llm+regras", segmento="varejo") == llm_before + 1

    unknown = copy.deepcopy(business_payload)
    unknown["segmento"] = "marte"
    outro = _metric(BUSINESS_ANALYSES, status="APROVADO", gerado_por="regras", segmento="outro")
    run_analysis(unknown)
    assert _metric(BUSINESS_ANALYSES, status="APROVADO", gerado_por="regras", segmento="outro") == outro + 1


def test_metric_series_zero_initialised():
    from prometheus_client import generate_latest

    text = generate_latest().decode()
    assert 'business_analyses_total{gerado_por="llm+regras",segmento="construcao",status="ANALISE_MANUAL"}' in text
    assert 'llm_requests_total{result="disabled"}' in text
