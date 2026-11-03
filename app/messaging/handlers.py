"""Handlers dos eventos consumidos (síncronos, testáveis sem broker)."""
import logging
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analysis.repository import BusinessAnalysisRepository
from app.analysis.service import run_analysis
from app.messaging.consumer import HandlerOutcome
from app.messaging.publisher import ANALYSIS_COMPLETED, analysis_completed_event
from app.models import CreditDecisionEvent, CustomerScoreStatus

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


class AnamnesisUpdatedEvent(BaseModel):
    event_id: str
    event_type: str = "customer.anamnesis_updated"
    occurred_at: datetime
    customer_id: int
    dividas_ativas: float
    comprometimento_renda: float


def handle_anamnesis_updated(db: Session, payload: dict) -> str:
    """Marca o score do cliente como desatualizado (sem token não dá para reavaliar)."""
    try:
        event = AnamnesisUpdatedEvent.model_validate(payload)
    except ValidationError as exc:
        logger.warning("evento customer.anamnesis_updated inválido: %s", exc.errors())
        return RESULT_INVALID

    status = db.get(CustomerScoreStatus, event.customer_id)
    if status is not None and status.last_event_id == event.event_id:
        return RESULT_DUPLICATE
    if status is None:
        status = CustomerScoreStatus(customer_id=event.customer_id)
        db.add(status)
    status.desatualizado = True
    status.dividas_ativas = event.dividas_ativas
    status.comprometimento_renda = event.comprometimento_renda
    status.last_event_id = event.event_id
    status.updated_at = datetime.now(timezone.utc)
    db.commit()
    return RESULT_OK


class OnboardingSubmittedEvent(BaseModel):
    event_id: str
    event_type: str = "customer.onboarding_submitted"
    occurred_at: datetime
    customer_id: int
    auth0_sub: str | None = None
    segmento: str
    empresa: dict | None = None
    respostas: dict = {}
    observacoes: str | None = None


def handle_onboarding_submitted(db: Session, payload: dict, llm=None) -> "str | HandlerOutcome":
    """Roda a análise do negócio (regras + LLM opcional), grava nova versão e devolve o
    `business.analysis_completed` para publicar após o commit. Idempotente por `event_id`."""
    try:
        event = OnboardingSubmittedEvent.model_validate(payload)
    except ValidationError as exc:
        logger.warning("evento customer.onboarding_submitted inválido: %s", exc.errors())
        return RESULT_INVALID

    repo = BusinessAnalysisRepository(db)
    existing = repo.by_event(event.event_id)
    if existing is not None:
        return _republish_if_current(repo, existing)
    db.rollback()  # libera a conexão do pool durante a análise (o LLM pode levar até 20 s)

    outcome = run_analysis(event.model_dump(mode="json"), llm=llm)
    try:
        repo.save_outcome(event.customer_id, outcome, source_event_id=event.event_id)
        db.commit()
    except IntegrityError:  # mesma mensagem processada em paralelo
        db.rollback()
        return RESULT_DUPLICATE
    logger.info(
        "análise do negócio do cliente %s: %s (score %s, %s)",
        event.customer_id, outcome.status, outcome.score, outcome.gerado_por,
    )
    completed = analysis_completed_event(
        event.customer_id, outcome.status, outcome.score, outcome.faixa_risco, outcome.gerado_por,
        source_event_id=event.event_id,
    )
    return HandlerOutcome(RESULT_OK, [(ANALYSIS_COMPLETED, completed)])


def _republish_if_current(repo: BusinessAnalysisRepository, row) -> "str | HandlerOutcome":
    """Reentrega de um evento já gravado (ex.: queda entre o commit e o publish): devolve o mesmo
    `business.analysis_completed` (mesmo `event_id`) se essa análise ainda é a versão vigente."""
    latest = repo.latest(row.customer_id)
    if latest is None or latest.id != row.id:
        return RESULT_DUPLICATE
    completed = analysis_completed_event(
        row.customer_id, row.status, row.score, row.faixa_risco, row.gerado_por,
        source_event_id=row.source_event_id,
    )
    return HandlerOutcome(RESULT_DUPLICATE, [(ANALYSIS_COMPLETED, completed)])


class OnboardingReviewedEvent(BaseModel):
    event_id: str
    event_type: str = "customer.onboarding_reviewed"
    occurred_at: datetime
    customer_id: int
    decisao: Literal["APROVADO", "REPROVADO"]
    comentario: str | None = None
    reviewer: str | None = None


def handle_onboarding_reviewed(db: Session, payload: dict) -> str:
    """Registra a decisão humana como nova versão da análise vigente. Idempotente por `event_id`."""
    try:
        event = OnboardingReviewedEvent.model_validate(payload)
    except ValidationError as exc:
        logger.warning("evento customer.onboarding_reviewed inválido: %s", exc.errors())
        return RESULT_INVALID

    repo = BusinessAnalysisRepository(db)
    if repo.exists_event(event.event_id):
        return RESULT_DUPLICATE
    latest = repo.latest(event.customer_id)
    if latest is None:
        logger.warning("revisão do cliente %s sem análise do negócio registrada", event.customer_id)
        return RESULT_INVALID

    resultado = {**(latest.resultado or {}), "status": event.decisao}
    try:
        repo.add_version(
            event.customer_id,
            status=event.decisao,
            score=latest.score,
            faixa_risco=latest.faixa_risco,
            gerado_por=latest.gerado_por,
            resultado=resultado,
            source_event_id=event.event_id,
            reviewer=event.reviewer,
            review_comment=event.comentario,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return RESULT_DUPLICATE
    return RESULT_OK
