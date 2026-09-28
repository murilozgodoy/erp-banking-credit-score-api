from app.schemas import CustomerFinancialData
from app.strategies.base import ScoreResult, ScoreStrategy


class SimpleRuleStrategy(ScoreStrategy):
    """Score baseado em regras simples: renda vs dívidas."""

    name = "simple"

    def calculate(self, data: CustomerFinancialData) -> ScoreResult:
        score = 500.0
        factors: list[dict] = []

        if data.renda_mensal >= 10000:
            score += 200
            factors.append({"nome": "renda_alta", "peso": 1.0, "valor": 200})
        elif data.renda_mensal >= 5000:
            score += 100
            factors.append({"nome": "renda_media", "peso": 1.0, "valor": 100})

        if data.dividas_ativas > data.renda_mensal * 3:
            score -= 200
            factors.append({"nome": "dividas_altas", "peso": -1.0, "valor": -200})

        if data.comprometimento_renda > 50:
            score -= 100
            factors.append({"nome": "comprometimento_alto", "peso": -1.0, "valor": -100})

        score = max(0.0, min(1000.0, score))
        return ScoreResult(score=score, faixa_risco=self._classify(score), factors=factors)
