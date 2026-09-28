from app.schemas import CustomerFinancialData
from app.strategies.base import ScoreStrategy
from app.strategies.simple_rule_strategy import SimpleRuleStrategy
from app.strategies.weighted_strategy import WeightedStrategy


def test_simple_rule_high_income_and_healthy():
    data = CustomerFinancialData(
        customer_id=1, renda_mensal=12000, patrimonio=100000,
        dividas_ativas=1000, comprometimento_renda=20,
    )
    result = SimpleRuleStrategy().calculate(data)
    assert result.score == 700
    assert result.faixa_risco == "MEDIO"
    assert any(f["nome"] == "renda_alta" for f in result.factors)


def test_simple_rule_mid_income():
    data = CustomerFinancialData(
        customer_id=2, renda_mensal=6000, patrimonio=0,
        dividas_ativas=500, comprometimento_renda=10,
    )
    result = SimpleRuleStrategy().calculate(data)
    assert result.score == 600
    assert any(f["nome"] == "renda_media" for f in result.factors)


def test_simple_rule_penalizes_high_debt_and_commitment():
    data = CustomerFinancialData(
        customer_id=3, renda_mensal=2000, patrimonio=0,
        dividas_ativas=20000, comprometimento_renda=80,
    )
    result = SimpleRuleStrategy().calculate(data)
    assert result.score == 200
    assert result.faixa_risco == "MUITO_ALTO"


def test_simple_rule_score_bounded_zero():
    data = CustomerFinancialData(
        customer_id=4, renda_mensal=0, patrimonio=0,
        dividas_ativas=999999, comprometimento_renda=100,
    )
    result = SimpleRuleStrategy().calculate(data)
    assert result.score >= 0
    assert result.score <= 1000


def test_weighted_top_scenario():
    data = CustomerFinancialData(
        customer_id=5, renda_mensal=20000, patrimonio=500000,
        dividas_ativas=1000, comprometimento_renda=0,
    )
    result = WeightedStrategy().calculate(data)
    assert result.score >= 900
    assert result.faixa_risco == "BAIXO"


def test_weighted_low_scenario_penalized_by_debt():
    data = CustomerFinancialData(
        customer_id=6, renda_mensal=1000, patrimonio=0,
        dividas_ativas=10000, comprometimento_renda=90,
    )
    result = WeightedStrategy().calculate(data)
    assert result.score < 500


def test_classify_boundaries():
    assert ScoreStrategy._classify(1000) == "BAIXO"
    assert ScoreStrategy._classify(750) == "BAIXO"
    assert ScoreStrategy._classify(749.99) == "MEDIO"
    assert ScoreStrategy._classify(500) == "MEDIO"
    assert ScoreStrategy._classify(499) == "ALTO"
    assert ScoreStrategy._classify(300) == "ALTO"
    assert ScoreStrategy._classify(0) == "MUITO_ALTO"
