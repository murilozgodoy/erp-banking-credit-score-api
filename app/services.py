from sqlalchemy.orm import Session

from app.clients.onboarding_client import OnboardingClient
from app.factories.score_strategy_factory import ScoreStrategyFactory
from app.models import ScoreEvaluation
from app.repositories import ScoreRepository


class ScoreNotFoundError(Exception):
    pass


class ScoreService:
    def __init__(
        self,
        db: Session,
        onboarding_client: OnboardingClient | None = None,
        factory: type[ScoreStrategyFactory] = ScoreStrategyFactory,
    ):
        self.db = db
        self.repo = ScoreRepository(db)
        self.onboarding_client = onboarding_client or OnboardingClient()
        self.factory = factory

    def evaluate(self, customer_id: int, strategy_name: str | None = None) -> ScoreEvaluation:
        data = self.onboarding_client.get_customer_financials(customer_id)
        strategy = self.factory.create(strategy_name)
        result = strategy.calculate(data)

        decision = self._decide(result.score, result.faixa_risco)

        return self.repo.save(
            customer_id=customer_id,
            score=result.score,
            faixa_risco=result.faixa_risco,
            strategy_name=strategy.name,
            factors=result.factors,
            decision=decision,
        )

    def latest(self, customer_id: int) -> ScoreEvaluation:
        latest = self.repo.latest_for_customer(customer_id)
        if latest is None:
            raise ScoreNotFoundError(f"Nenhum score encontrado para cliente {customer_id}")
        return latest

    @staticmethod
    def _decide(score: float, faixa_risco: str) -> tuple[str, str]:
        if faixa_risco == "BAIXO":
            return ("APROVADO", "Score suficiente para aprovação automática")
        if faixa_risco == "MEDIO":
            return ("REVISAO", "Score exige revisão manual")
        return ("NEGADO", f"Faixa de risco {faixa_risco}")
