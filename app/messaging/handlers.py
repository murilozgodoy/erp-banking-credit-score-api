"""Handlers dos eventos consumidos (síncronos, testáveis sem broker)."""
import logging
from datetime import datetime, timezone

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CreditDecisionEvent

logger = logging.getLogger(__name__)

RESULT_OK = "ok"
RESULT_DUPLICATE = "duplicate"
RESULT_INVALID = "invalid"


class CreditDecidedEvent(BaseModel):
    event_id: str
    event_type: str = "credit.decided"
    occurred_at: datetime
    credit_request_id: int
    customer_id: int
    valor_solicitado: float
    prazo_meses: int
    score: float | None = None
    faixa_risco: str | None = None
    status: str
    motivo: str | None = None
    taxa_juros: float | None = None


def handle_credit_decided(db: Session, payload: dict) -> str:
    """Persiste o evento `credit.decided`; idempotente por `event_id`."""
    try:
        event = CreditDecidedEvent.model_validate(payload)
    except ValidationError as exc:
        logger.warning("evento credit.decided inválido: %s", exc.errors())
        return RESULT_INVALID

    exists = db.scalar(
        select(CreditDecisionEvent.id).where(CreditDecisionEvent.event_id == event.event_id)
    )
    if exists is not None:
        return RESULT_DUPLICATE

    db.add(
        CreditDecisionEvent(
            event_id=event.event_id,
            credit_request_id=event.credit_request_id,
            customer_id=event.customer_id,
            status=event.status,
            motivo=event.motivo,
            valor_solicitado=event.valor_solicitado,
            prazo_meses=event.prazo_meses,
            score=event.score,
            faixa_risco=event.faixa_risco,
            taxa_juros=event.taxa_juros,
            occurred_at=event.occurred_at,
            received_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return RESULT_OK
