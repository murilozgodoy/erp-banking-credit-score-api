from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
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
