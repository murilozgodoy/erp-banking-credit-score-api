from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import CreditDecision, ScoreEvaluation, ScoreFactor


class ScoreRepository:
    def __init__(self, db: Session):
        self.db = db

    def save(
        self,
        customer_id: int,
        score: float,
        faixa_risco: str,
        strategy_name: str,
        factors: list[dict],
        decision: tuple[str, str] | None = None,
    ) -> ScoreEvaluation:
        evaluation = ScoreEvaluation(
            customer_id=customer_id,
            score=score,
            faixa_risco=faixa_risco,
            strategy_usada=strategy_name,
        )
        for f in factors:
            evaluation.factors.append(
                ScoreFactor(nome=f["nome"], peso=f["peso"], valor=f["valor"])
            )
        if decision is not None:
            evaluation.decision = CreditDecision(decisao=decision[0], motivo=decision[1])

        self.db.add(evaluation)
        self.db.commit()
        self.db.refresh(evaluation)
        return evaluation

    def latest_for_customer(self, customer_id: int) -> ScoreEvaluation | None:
        stmt = (
            select(ScoreEvaluation)
            .where(ScoreEvaluation.customer_id == customer_id)
            .order_by(desc(ScoreEvaluation.created_at), desc(ScoreEvaluation.id))
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def list_for_customer(self, customer_id: int, limit: int = 20) -> list[ScoreEvaluation]:
        stmt = (
            select(ScoreEvaluation)
            .where(ScoreEvaluation.customer_id == customer_id)
            .order_by(desc(ScoreEvaluation.created_at), desc(ScoreEvaluation.id))
            .limit(limit)
        )
        return list(self.db.scalars(stmt))

    def get(self, evaluation_id: int) -> ScoreEvaluation | None:
        return self.db.get(ScoreEvaluation, evaluation_id)
