from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.schemas import CustomerFinancialData


@dataclass
class ScoreResult:
    score: float
    faixa_risco: str
    factors: list[dict] = field(default_factory=list)


class ScoreStrategy(ABC):
    """Interface do padrão Strategy para cálculo de score."""

    name: str = "base"

    @abstractmethod
    def calculate(self, data: CustomerFinancialData) -> ScoreResult:
        raise NotImplementedError

    @staticmethod
    def _classify(score: float) -> str:
        if score >= 750:
            return "BAIXO"
        if score >= 500:
            return "MEDIO"
        if score >= 300:
            return "ALTO"
        return "MUITO_ALTO"
