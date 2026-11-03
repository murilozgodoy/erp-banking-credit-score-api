import copy

import pytest

from app.analysis.base import AREAS, KPI_IDS, SEVERIDADES, TIPOS_OPORTUNIDADE
from app.analysis.benchmarks import BENCHMARKS, SEGMENTOS, benchmark_for
from app.analysis.rules import (
    RuleBasedAnalyzer,
    _bool,
    _leitura,
    _num,
    _sazonalidade,
    parcela_price,
    taxa_mensal,
    valor_presente,
)


def analyze(payload):
    return RuleBasedAnalyzer().analyze(payload)


def with_answers(payload, **respostas):
    p = copy.deepcopy(payload)
    p["respostas"].update(respostas)
    return p


def kpi(result, kpi_id):
    return next(k for k in result["kpis"] if k["id"] == kpi_id)


def test_healthy_retail_full_result(business_payload):
    r = analyze(business_payload)
    assert [k["id"] for k in r["kpis"]] == list(KPI_IDS)
    assert 0 <= r["score"] <= 1000
    assert r["faixa_risco"] in ("BAIXO", "MEDIO")
    assert r["regras_criticas"] == []
    receita = kpi(r, "receita_mensal")
    assert receita == {
        "id": "receita_mensal", "label": "Receita mensal", "valor": 182000, "unidade": "BRL",
        "benchmark_segmento": 150000, "leitura": "acima da média do segmento (favorável)",
    }
    assert kpi(r, "endividamento_sobre_receita")["valor"] == pytest.approx(0.33, abs=0.01)
    assert kpi(r, "comprometimento_parcelas_pct")["valor"] == pytest.approx(4.9, abs=0.1)
    # cobertura = estoque / (CMV diário) = 120000 / (182000 * 0.58 / 30)
    assert kpi(r, "cobertura_estoque_dias")["valor"] == pytest.approx(34.1, abs=0.1)
    assert kpi(r, "ciclo_caixa_dias")["valor"] == pytest.approx(38 + 34.1 - 45, abs=0.2)
    areas = {i["area"] for i in r["insights"]}
    assert areas == set(AREAS)
    assert all(i["severidade"] in SEVERIDADES for i in r["insights"])
    assert {o["tipo"] for o in r["oportunidades"]} <= set(TIPOS_OPORTUNIDADE)
    assert r["pontos_fortes"]
    assert "ErpIa" in r["resumo"] and "Loja Exemplo" in r["resumo"]


def test_opportunity_limits_and_pricing(business_payload):
    r = analyze(business_payload)
    faixa = r["faixa_risco"]
    ofertas = {o["tipo"]: o for o in r["oportunidades"]}
    # antecipação: ≤ 80% dos recebíveis de cartão (182000 * 62% * 38/30)
    recebiveis = 182000 * 0.62 * 38 / 30
    assert 0 < ofertas["ANTECIPACAO_RECEBIVEIS"]["valor_maximo"] <= 0.8 * recebiveis
    assert ofertas["ANTECIPACAO_RECEBIVEIS"]["taxa_mensal"] == round(taxa_mensal(faixa, "CAPITAL_DE_GIRO") - 0.4, 2)
    assert ofertas["FINANCIAMENTO_ESTOQUE"]["valor_maximo"] == 72000  # 60% de 120000
    assert ofertas["FINANCIAMENTO_ESTOQUE"]["taxa_mensal"] == round(taxa_mensal(faixa, "CAPITAL_DE_GIRO") - 0.2, 2)
    assert ofertas["ALAVANCAGEM"]["valor_maximo"] <= 3 * 182000
    assert ofertas["ALAVANCAGEM"]["taxa_mensal"] == round(taxa_mensal(faixa, "CAPITAL_DE_GIRO") + 0.3, 2)
    assert ofertas["CAPITAL_DE_GIRO"]["valor_maximo"] <= 182000
    for o in r["oportunidades"]:
        assert set(o) == {"tipo", "titulo", "descricao", "valor_maximo", "taxa_mensal", "prazo_max_meses", "racional"}
        assert o["valor_maximo"] % 100 == 0


@pytest.mark.parametrize("faixa,produto,taxa", [
    ("BAIXO", "CAPITAL_DE_GIRO", 1.69),
    ("MEDIO", "ANTECIPACAO_RECEBIVEIS", 2.09),
    ("ALTO", "ALAVANCAGEM", 3.79),
    ("MEDIO", "FINANCIAMENTO_ESTOQUE", 2.29),
    ("MUITO_ALTO", "CAPITAL_DE_GIRO", None),
])
def test_taxa_mensal(faixa, produto, taxa):
    assert taxa_mensal(faixa, produto) == taxa


def test_price_helpers_are_inverse():
    pv = valor_presente(1000, 2.0, 12)
    assert parcela_price(pv, 2.0, 12) == pytest.approx(1000)


def test_excessive_debt_is_critical_and_offers_consolidation(business_payload):
    r = analyze(with_answers(business_payload, dividas_total=600000, parcela_dividas_mensal=40000))
    assert "DIVIDA_EXCESSIVA" in r["regras_criticas"]
    assert any("3x" in risco for risco in r["riscos"])
    tipos = {o["tipo"] for o in r["oportunidades"]}
    assert "ALAVANCAGEM" not in tipos
    if r["faixa_risco"] != "MUITO_ALTO":
        assert "CONSOLIDACAO_DIVIDAS" in tipos


def test_high_installments_critical(business_payload):
    r = analyze(with_answers(business_payload, parcela_dividas_mensal=100000))
    assert "PARCELAS_EXCESSIVAS" in r["regras_criticas"]


@pytest.mark.parametrize("receita,score", [(0, 150), (None, 150), (-10, 0), ("abc", 150)])
def test_invalid_revenue(business_payload, receita, score):
    r = analyze(with_answers(business_payload, faturamento_mensal=receita))
    assert r["score"] == score
    assert r["faixa_risco"] == "MUITO_ALTO"
    assert {"RECEITA_INVALIDA", "RISCO_MUITO_ALTO"} <= set(r["regras_criticas"])
    assert r["oportunidades"] == []
    assert any(i["area"] == "geral" and i["severidade"] == "atencao" for i in r["insights"])


def test_negative_margin_critical(business_payload):
    r = analyze(with_answers(business_payload, margem_bruta_pct=-5))
    assert "MARGEM_NEGATIVA" in r["regras_criticas"]


def test_minimal_payload_is_robust():
    r = analyze({"customer_id": 1, "segmento": "servicos", "respostas": {"faturamento_mensal": "80.000,00"}})
    assert kpi(r, "receita_mensal")["valor"] == 80000
    assert kpi(r, "margem_bruta_pct") == {
        "id": "margem_bruta_pct", "label": "Margem bruta", "valor": None, "unidade": "%",
        "benchmark_segmento": 50, "leitura": "não informado",
    }
    assert "cobertura_estoque_dias" not in {k["id"] for k in r["kpis"]}
    assert r["oportunidades"]  # capital de giro, antecipação pelo benchmark de cartão
    assert any(i["titulo"] == "Informe sua margem" for i in r["insights"])


def test_garbage_payload_does_not_crash():
    r = analyze({"segmento": None, "respostas": "x", "empresa": "y"})
    assert r["faixa_risco"] == "MUITO_ALTO"
    assert "A empresa" in r["resumo"]


def test_weak_business_risks_and_attention(business_payload):
    p = with_answers(
        business_payload, faturamento_mensal=40000, margem_bruta_pct=10, prazo_medio_recebimento_dias=90,
        prazo_medio_pagamento_dias=10, folha_mensal=25000, dividas_total=100000, parcela_dividas_mensal=14000,
        valor_estoque=200000, sazonalidade="Alta", pct_cartao_credito=20, ticket_medio=None,
    )
    r = analyze(p)
    assert r["faixa_risco"] in ("ALTO", "MUITO_ALTO")
    texto = " ".join(r["riscos"])
    for trecho in ("Margem bruta", "Dívidas equivalem", "Parcelas comprometem", "Ciclo de caixa", "Folha", "Sazonalidade", "Estoque cobre"):
        assert trecho in texto
    por_area = {i["area"]: i for i in r["insights"]}
    assert por_area["pagamentos"]["titulo"] == "Paga antes de receber"
    assert por_area["estoque"]["titulo"] == "Estoque acima do necessário"
    assert por_area["vendas"]["titulo"] == "Receita frente ao segmento"


def test_saas_churn_penalty():
    base = {"segmento": "tecnologia", "respostas": {"faturamento_mensal": 100000, "margem_bruta_pct": 75}}
    ok = analyze(base)
    churn = analyze({**base, "respostas": {**base["respostas"], "churn_mensal_pct": 8}})
    assert churn["score"] == ok["score"] - 40
    assert any("Churn" in r for r in churn["riscos"])


def test_insight_variants():
    # sem prazos: pagamentos pela folha; recebíveis pela antecipação; pendências por dívidas sem parcela
    r = analyze({"segmento": "alimentacao", "respostas": {
        "faturamento_mensal": 90000, "folha_mensal": 60000, "dividas_total": 30000, "ticket_medio": 60,
        "possui_estoque": "sim", "valor_estoque": 1000,
    }})
    por_area = {i["area"]: i for i in r["insights"]}
    assert por_area["pagamentos"]["titulo"] == "Peso da folha"
    assert por_area["recebiveis"]["severidade"] == "oportunidade"
    assert por_area["pendencias"]["titulo"] == "Dívidas em aberto"
    assert por_area["vendas"]["titulo"] == "Ticket médio"
    assert por_area["estoque"]["severidade"] == "oportunidade"


def test_negative_cycle_is_strength():
    r = analyze({"segmento": "alimentacao", "respostas": {
        "faturamento_mensal": 90000, "prazo_medio_recebimento_dias": 2, "prazo_medio_pagamento_dias": 30,
    }})
    assert any("Ciclo de caixa negativo" in p for p in r["pontos_fortes"])
    assert {i["area"]: i for i in r["insights"]}["pagamentos"]["titulo"] == "Prazo de pagamento saudável"


def test_stock_inferred_without_flag():
    r = analyze({"segmento": "varejo", "respostas": {"faturamento_mensal": 100000, "valor_estoque": 50000}})
    assert "cobertura_estoque_dias" in {k["id"] for k in r["kpis"]}


def test_stock_flag_false_without_value():
    r = analyze({"segmento": "varejo", "respostas": {"faturamento_mensal": 100000, "possui_estoque": False}})
    assert "cobertura_estoque_dias" not in {k["id"] for k in r["kpis"]}


def test_estoque_cobertura_info_when_no_offer():
    # faixa MUITO_ALTO (sem ofertas) e cobertura normal → insight informativo
    r = analyze({"segmento": "varejo", "respostas": {
        "faturamento_mensal": 100000, "margem_bruta_pct": -20, "possui_estoque": True, "valor_estoque": 50000,
        "dividas_total": 900000, "parcela_dividas_mensal": 80000,
    }})
    assert r["oportunidades"] == []
    por_area = {i["area"]: i for i in r["insights"]}
    assert por_area["estoque"]["titulo"] == "Cobertura de estoque"
    assert por_area["credito"]["titulo"] == "Sem oferta de crédito no momento"


def test_all_segments_have_benchmarks():
    assert set(BENCHMARKS) == set(SEGMENTOS)
    assert benchmark_for("inexistente") == BENCHMARKS["outro"]
    assert benchmark_for(None) == BENCHMARKS["outro"]


@pytest.mark.parametrize("value,expected", [
    (10, 10.0), ("1.234,56", 1234.56), ("R$ 1500", 1500.0), ("12,5%", 12.5), ("", None),
    (True, None), (float("nan"), None), ([1], None), ("inf", None),
])
def test_num(value, expected):
    assert _num(value) == expected


@pytest.mark.parametrize("value,expected", [
    (True, True), ("Sim", True), ("não", False), (1, True), (0, False), (None, False),
])
def test_bool(value, expected):
    assert _bool(value) is expected


@pytest.mark.parametrize("value,expected", [
    ("Alta", "alta"), ("Média", "media"), ("moderada", "media"), ("Baixa", "baixa"),
    ("Sem sazonalidade", "baixa"), ("??", None), (3, None),
])
def test_sazonalidade(value, expected):
    assert _sazonalidade(value) == expected


def test_leitura_variants():
    assert _leitura(None, 10, True) == "não informado"
    assert _leitura(0, 0, False) == "em linha com a média do segmento"
    assert _leitura(5, 0, False) == "acima da média do segmento"
    assert _leitura(-5, 0, False) == "abaixo da média do segmento"
    assert _leitura(105, 100, True) == "em linha com a média do segmento"
    assert _leitura(50, 100, False) == "abaixo da média do segmento (favorável)"
    assert _leitura(50, 100, None) == "abaixo da média do segmento"
