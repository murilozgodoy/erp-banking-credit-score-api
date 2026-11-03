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


class StrategyOut(BaseModel):
    name: str
    description: str
    default: bool


class CustomerFinancialData(BaseModel):
    """Snapshot dos dados do cliente que a Strategy consome."""

    customer_id: int
    renda_mensal: float = 0.0
    patrimonio: float = 0.0
    dividas_ativas: float = 0.0
    comprometimento_renda: float = 0.0


class DecisionEventOut(BaseModel):
    """Decisão de crédito registrada via evento `credit.decided` (contrato seção 2.4)."""

    model_config = ConfigDict(from_attributes=True)
    id: int
    event_id: str
    credit_request_id: int
    customer_id: int
    status: str
    motivo: str | None = None
    valor_solicitado: float
    prazo_meses: int
    score: float | None = None
    faixa_risco: str | None = None
    taxa_juros: float | None = None
    occurred_at: datetime
    received_at: datetime


class ScoreStatusOut(BaseModel):
    """Situação do score: desatualizado quando a anamnese mudou depois da última avaliação."""

    model_config = ConfigDict(from_attributes=True)
    customer_id: int
    desatualizado: bool = False
    motivo: str | None = None
    dividas_ativas: float | None = None
    comprometimento_renda: float | None = None
    updated_at: datetime | None = None
