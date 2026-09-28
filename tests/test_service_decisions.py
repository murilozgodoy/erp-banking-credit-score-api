from app.services import ScoreService


def test_decide_baixo_aprovado():
    d, m = ScoreService._decide(800, "BAIXO")
    assert d == "APROVADO"


def test_decide_medio_revisao():
    d, m = ScoreService._decide(600, "MEDIO")
    assert d == "REVISAO"


def test_decide_alto_negado():
    d, m = ScoreService._decide(400, "ALTO")
    assert d == "NEGADO"


def test_decide_muito_alto_negado():
    d, m = ScoreService._decide(100, "MUITO_ALTO")
    assert d == "NEGADO"
    assert "MUITO_ALTO" in m
