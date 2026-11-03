from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ScoreEvaluation(Base):
    __tablename__ = "score_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    faixa_risco: Mapped[str] = mapped_column(String(20), nullable=False)
    strategy_usada: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    factors: Mapped[list["ScoreFactor"]] = relationship(
        "ScoreFactor", back_populates="evaluation", cascade="all, delete-orphan"
    )
    decision: Mapped["CreditDecision | None"] = relationship(
        "CreditDecision", back_populates="evaluation", uselist=False, cascade="all, delete-orphan"
    )


class ScoreFactor(Base):
    __tablename__ = "score_factors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("score_evaluations.id", ondelete="CASCADE"), nullable=False
    )
    nome: Mapped[str] = mapped_column(String(40), nullable=False)
    peso: Mapped[float] = mapped_column(Float, nullable=False)
    valor: Mapped[float] = mapped_column(Float, nullable=False)

    evaluation: Mapped[ScoreEvaluation] = relationship("ScoreEvaluation", back_populates="factors")


class CreditDecision(Base):
    __tablename__ = "credit_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("score_evaluations.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    decisao: Mapped[str] = mapped_column(String(20), nullable=False)
    motivo: Mapped[str] = mapped_column(String(200), nullable=False)

    evaluation: Mapped[ScoreEvaluation] = relationship("ScoreEvaluation", back_populates="decision")


class CreditDecisionEvent(Base):
    """Decisão de crédito recebida do Transactions via evento `credit.decided`."""

    __tablename__ = "credit_decision_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    credit_request_id: Mapped[int] = mapped_column(Integer, nullable=False)
    customer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    motivo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    valor_solicitado: Mapped[float] = mapped_column(Float, nullable=False)
    prazo_meses: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    faixa_risco: Mapped[str | None] = mapped_column(String(20), nullable=True)
    taxa_juros: Mapped[float | None] = mapped_column(Float, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CustomerScoreStatus(Base):
    """Marca o score do cliente como desatualizado quando a anamnese muda no Onboarding."""

    __tablename__ = "customer_score_status"

    customer_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    desatualizado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dividas_ativas: Mapped[float | None] = mapped_column(Float, nullable=True)
    comprometimento_renda: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BusinessAnalysis(Base):
    """Análise do negócio (onboarding PJ), versionada por cliente: a maior `version` é a vigente."""

    __tablename__ = "business_analyses"
    __table_args__ = (UniqueConstraint("customer_id", "version", name="uq_business_analyses_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    faixa_risco: Mapped[str | None] = mapped_column(String(20), nullable=True)
    gerado_por: Mapped[str] = mapped_column(String(20), nullable=False)
    resultado: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_event_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
