from app.schemas import CustomerFinancialData
from app.strategies.base import ScoreResult, ScoreStrategy


class WeightedStrategy(ScoreStrategy):
    """Score ponderado: renda 40%, patrimônio 30%, comprometimento 30%."""

    name = "weighted"

    WEIGHTS = {"renda": 0.40, "patrimonio": 0.30, "comprometimento": 0.30}

    def calculate(self, data: CustomerFinancialData) -> ScoreResult:
        renda_score = min(1000.0, (data.renda_mensal / 20000.0) * 1000.0)
        patrimonio_score = min(1000.0, (data.patrimonio / 500000.0) * 1000.0)
        comprometimento_score = max(0.0, 1000.0 - (data.comprometimento_renda * 10.0))

        score = (
            renda_score * self.WEIGHTS["renda"]
            + patrimonio_score * self.WEIGHTS["patrimonio"]
            + comprometimento_score * self.WEIGHTS["comprometimento"]
        )

        if data.dividas_ativas > data.renda_mensal * 5:
            score = max(0.0, score - 150.0)

        score = round(max(0.0, min(1000.0, score)), 2)

        factors = [
            {"nome": "renda", "peso": self.WEIGHTS["renda"], "valor": round(renda_score, 2)},
            {"nome": "patrimonio", "peso": self.WEIGHTS["patrimonio"], "valor": round(patrimonio_score, 2)},
            {"nome": "comprometimento", "peso": self.WEIGHTS["comprometimento"], "valor": round(comprometimento_score, 2)},
        ]
        return ScoreResult(score=score, faixa_risco=self._classify(score), factors=factors)
