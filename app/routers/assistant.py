"""Assistente ErpIa: `POST /me/assistant/messages` (contrato seção 9 do PORTAL_CLIENTE.md).

Rota síncrona de propósito: o FastAPI a roda no threadpool, então o loop síncrono com o Claude e as chamadas ao
Transactions não bloqueiam o event loop. Nos logs só entram resultado, latência, número de ferramentas e modelo.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.analysis.repository import latest_analysis_body
from app.assistant.agent import AssistantService, AssistantUnavailable, build_assistant_service
from app.assistant.rate_limit import SlidingWindowLimiter, rate_limiter
from app.assistant.tools import ToolExecutor
from app.auth import Principal, require_permission
from app.clients.transactions_client import TransactionsClient
from app.config import settings
from app.database import get_db
from app.metrics import ASSISTANT_MESSAGES, ASSISTANT_REQUEST_DURATION
from app.routers.analysis import _resolve_me

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assistant"])

MAX_MESSAGES = 20
MAX_CONTENT_CHARS = 2000


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_CONTENT_CHARS)


class AssistantRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    rota: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _last_is_user(self):
        if self.messages[-1].role != "user":
            raise ValueError("a última mensagem deve ser do usuário")
        return self


class Fonte(BaseModel):
    tipo: Literal["analise", "overview", "vendas", "recebiveis", "pagamentos", "estoque", "pendencias", "credito"]
    label: str


class Acao(BaseModel):
    label: str
    rota: str


class AssistantResponse(BaseModel):
    resposta: str
    sugestoes: list[str]
    fontes: list[Fonte]
    acoes: list[Acao]


_service: AssistantService | None = None
_service_lock = threading.Lock()


def get_assistant_service() -> AssistantService | None:
    """Criado uma vez (client HTTP reaproveitado); `None` sem `ANTHROPIC_API_KEY`."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = build_assistant_service(settings)
    return _service


def get_rate_limiter() -> SlidingWindowLimiter:
    return rate_limiter


def _error(status_code: int, body: dict, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=body, headers=headers)


ERRORS = {
    409: {"description": 'Onboarding não aprovado: `{"error":"ONBOARDING_PENDENTE","status":...}`'},
    429: {"description": '`{"error":"LIMITE_ASSISTENTE"}` (10 mensagens/min por usuário)'},
    503: {"description": '`{"error":"ASSISTENTE_INDISPONIVEL"}` (sem chave, falha ou prazo do LLM)'},
}


@router.post("/me/assistant/messages", response_model=AssistantResponse, responses=ERRORS)
def post_assistant_message(
    body: AssistantRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission("read:self")),
    service: AssistantService | None = Depends(get_assistant_service),
    limiter: SlidingWindowLimiter = Depends(get_rate_limiter),
):
    """Pergunta ao Assistente ErpIa sobre a empresa do cliente dono do token.

    Ferramentas só de leitura e simulação no Transactions (token repassado) e a análise do próprio Score; até 6
    chamadas de ferramenta e 45 s por resposta. Nunca executa pagamento, contratação ou escrita.
    """
    started = time.perf_counter()
    result, tool_calls, ran = "error", 0, False
    try:
        if service is None:
            result = "disabled"
            return _error(503, {"error": "ASSISTENTE_INDISPONIVEL"})
        allowed, retry_after = limiter.acquire(principal.subject)
        if not allowed:
            result = "limited"
            return _error(429, {"error": "LIMITE_ASSISTENTE"}, {"Retry-After": str(retry_after)})
        me = _resolve_me(principal)
        if me.customer_id is not None and me.onboarding_status != "APROVADO":
            me = _resolve_me(principal, fresh=True)  # o status em cache pode ser anterior à aprovação
        if me.customer_id is None or me.onboarding_status != "APROVADO":
            result = "pendente"
            return _error(409, {"error": "ONBOARDING_PENDENTE", "status": me.onboarding_status or "NAO_INICIADO"})

        customer_id = me.customer_id
        tools = ToolExecutor(
            TransactionsClient(access_token=principal.token),
            analysis_provider=lambda: latest_analysis_body(db, customer_id),
        )
        ran = True
        try:
            outcome = service.answer([m.model_dump() for m in body.messages], body.rota, tools)
        except AssistantUnavailable as exc:
            result, tool_calls = exc.kind, exc.tool_calls
            return _error(503, {"error": "ASSISTENTE_INDISPONIVEL"})
        result, tool_calls = outcome.result, outcome.tool_calls
        return outcome.reply
    finally:
        elapsed = time.perf_counter() - started
        ASSISTANT_MESSAGES.labels(result=result).inc()
        if ran:
            ASSISTANT_REQUEST_DURATION.observe(elapsed)
        logger.info(
            "assistente result=%s model=%s latency_ms=%d tool_calls=%d",
            result,
            service.model if service is not None else "-",
            int(elapsed * 1000),
            tool_calls,
        )
