"""Persistência das análises do negócio (`business_analyses`, versionada por cliente)."""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.service import AnalysisOutcome
from app.models import BusinessAnalysis


class BusinessAnalysisRepository:
    def __init__(self, db: Session):
        self.db = db

    def latest(self, customer_id: int) -> BusinessAnalysis | None:
        stmt = (
            select(BusinessAnalysis)
            .where(BusinessAnalysis.customer_id == customer_id)
            .order_by(BusinessAnalysis.version.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def by_event(self, event_id: str) -> BusinessAnalysis | None:
        return self.db.scalars(
            select(BusinessAnalysis).where(BusinessAnalysis.source_event_id == event_id)
        ).first()

    def exists_event(self, event_id: str) -> bool:
        stmt = select(BusinessAnalysis.id).where(BusinessAnalysis.source_event_id == event_id)
        return self.db.scalar(stmt) is not None

    def _next_version(self, customer_id: int) -> int:
        stmt = select(func.max(BusinessAnalysis.version)).where(
            BusinessAnalysis.customer_id == customer_id
        )
        return (self.db.scalar(stmt) or 0) + 1

    def add_version(
        self,
        customer_id: int,
        *,
        status: str,
        score: int | None,
        faixa_risco: str | None,
        gerado_por: str,
        resultado: dict,
        source_event_id: str | None = None,
        reviewer: str | None = None,
        review_comment: str | None = None,
    ) -> BusinessAnalysis:
        """Cria a próxima versão (sem commit: quem chama controla a transação)."""
        row = BusinessAnalysis(
            customer_id=customer_id,
            version=self._next_version(customer_id),
            status=status,
            score=score,
            faixa_risco=faixa_risco,
            gerado_por=gerado_por,
            resultado=resultado,
            source_event_id=source_event_id,
            reviewer=reviewer,
            review_comment=review_comment,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def save_outcome(
        self, customer_id: int, outcome: AnalysisOutcome, source_event_id: str | None = None
    ) -> BusinessAnalysis:
        body = {**outcome.body, "customer_id": customer_id}
        return self.add_version(
            customer_id,
            status=outcome.status,
            score=outcome.score,
            faixa_risco=outcome.faixa_risco,
            gerado_por=outcome.gerado_por,
            resultado=body,
            source_event_id=source_event_id,
        )


def to_response(row: BusinessAnalysis) -> dict:
    """Corpo do contrato (seção 4) a partir da versão persistida."""
    body = dict(row.resultado or {})
    created = row.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    body.update(
        customer_id=row.customer_id,
        status=row.status,
        score=row.score,
        faixa_risco=row.faixa_risco,
        gerado_por=row.gerado_por,
        atualizado_em=created.isoformat() if created else None,
    )
    for key in ("kpis", "insights", "oportunidades", "pontos_fortes", "riscos"):
        body.setdefault(key, [])
    body.setdefault("resumo", None)
    return body


def em_analise_response(customer_id: int) -> dict:
    """Resposta para cliente com onboarding enviado e análise ainda não concluída."""
    return {
        "customer_id": customer_id,
        "status": "EM_ANALISE",
        "score": None,
        "faixa_risco": None,
        "resumo": None,
        "gerado_por": None,
        "atualizado_em": None,
        "kpis": [],
        "insights": [],
        "oportunidades": [],
        "pontos_fortes": [],
        "riscos": [],
    }
