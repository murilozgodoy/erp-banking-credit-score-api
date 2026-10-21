from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import Principal, require_permission
from app.clients.onboarding_client import (
    CustomerNotFoundInOnboarding,
    OnboardingClient,
    OnboardingClientError,
    OnboardingForbiddenError,
)
from app.database import get_db
from app.factories.score_strategy_factory import UnknownStrategyError
from app.schemas import EvaluationOut, EvaluationRequest
from app.services import ScoreNotFoundError, ScoreService

router = APIRouter(prefix="/scores", tags=["scores"])


@router.post(
    "/evaluations",
    response_model=EvaluationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_evaluation(
    payload: EvaluationRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("write:scores")),
):
    service = ScoreService(db, onboarding_client=OnboardingClient(access_token=principal.token))
    try:
        evaluation = service.evaluate(payload.customer_id, payload.strategy)
    except CustomerNotFoundInOnboarding as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except OnboardingForbiddenError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except UnknownStrategyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except OnboardingClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return evaluation


@router.get(
    "/customers/{customer_id}/latest",
    response_model=EvaluationOut,
    dependencies=[Depends(require_permission("read:scores"))],
)
def get_latest_score(customer_id: int, db: Session = Depends(get_db)):
    service = ScoreService(db)
    try:
        return service.latest(customer_id)
    except ScoreNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
