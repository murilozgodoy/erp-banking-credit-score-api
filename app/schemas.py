from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ScoreFactorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    nome: str
    peso: float
    valor: float


class CreditDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    decisao: str
    motivo: str


class EvaluationRequest(BaseModel):
    customer_id: int = Field(..., gt=0)
    strategy: str | None = Field(default=None, description="simple | weighted; padrão: weighted")


class EvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
    score: float
    faixa_risco: str
    strategy_usada: str
    created_at: datetime
    factors: list[ScoreFactorOut]
    decision: CreditDecisionOut | None = None


class CustomerFinancialData(BaseModel):
    """Snapshot dos dados do cliente que a Strategy consome."""

    customer_id: int
    renda_mensal: float = 0.0
    patrimonio: float = 0.0
    dividas_ativas: float = 0.0
    comprometimento_renda: float = 0.0
