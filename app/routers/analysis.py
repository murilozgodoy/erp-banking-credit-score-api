"""Análise do negócio: visão do cliente (`/me/analysis`) e do staff (`/scores/analyses/{id}`)."""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.analysis.repository import BusinessAnalysisRepository, em_analise_response, to_response
from app.auth import Principal, require_permission
from app.clients.onboarding_client import (
    OnboardingClient,
    OnboardingClientError,
    OnboardingForbiddenError,
    me_cache,
)
from app.database import get_db
from app.schemas import BusinessAnalysisOut

router = APIRouter(tags=["analysis"])

NOT_FOUND = {404: {"description": "Cliente sem onboarding ou sem análise"}}


def _resolve_me(principal: Principal, fresh: bool = False):
    client = OnboardingClient(access_token=principal.token)
    try:
        return me_cache.get(principal.subject, client.get_me, fresh=fresh)
    except OnboardingForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    except OnboardingClientError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


@router.get("/me/analysis", response_model=BusinessAnalysisOut, responses=NOT_FOUND)
def get_my_analysis(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("read:self")),
):
    """Análise vigente do cliente dono do token (customer resolvido pelo Onboarding `GET /me`).

    Sem customer → 404 `{"error":"ONBOARDING_NAO_INICIADO"}`; onboarding enviado e análise ainda
    não concluída → 200 com `status: "EM_ANALISE"`; demais casos sem análise → 404
    `{"error":"ANALISE_NAO_ENCONTRADA","status":<onboarding_status>}`.
    """
    me = _resolve_me(principal)
    if me.customer_id is None:
        return JSONResponse(
            status_code=404,
            content={"error": "ONBOARDING_NAO_INICIADO", "status": me.onboarding_status or "NAO_INICIADO"},
        )
    row = BusinessAnalysisRepository(db).latest(me.customer_id)
    if row is not None:
        return to_response(row)
    if me.onboarding_status != "EM_ANALISE":
        me = _resolve_me(principal, fresh=True)  # o status em cache pode ser anterior ao envio
    if me.onboarding_status == "EM_ANALISE":
        return em_analise_response(me.customer_id)
    return JSONResponse(
        status_code=404,
        content={"error": "ANALISE_NAO_ENCONTRADA", "status": me.onboarding_status},
    )


@router.get(
    "/scores/analyses/{customer_id}",
    response_model=BusinessAnalysisOut,
    responses=NOT_FOUND,
    dependencies=[Depends(require_permission("read:scores"))],
)
def get_customer_analysis(customer_id: int, db: Session = Depends(get_db)):
    """Versão mais recente da análise do negócio do cliente (staff)."""
    row = BusinessAnalysisRepository(db).latest(customer_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nenhuma análise para o cliente {customer_id}")
    return to_response(row)
