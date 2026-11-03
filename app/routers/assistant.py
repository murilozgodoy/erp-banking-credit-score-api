"""Assistente ErpIa: `POST /me/assistant/messages` e o histórico (contrato: seções 9 e 10 do PORTAL_CLIENTE.md).

Rota síncrona de propósito: o FastAPI a roda no threadpool, então o loop síncrono com o Claude e as chamadas ao
Transactions não bloqueiam o event loop. Nos logs só entram resultado, latência, número de ferramentas e modelo.

O pool do Postgres é pequeno: o POST usa duas transações curtas (pergunta + histórico; resposta) e nenhuma sessão
fica aberta durante a chamada ao LLM. A ferramenta `consultar_analise` abre a própria sessão curta.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.analysis.repository import latest_analysis_body
from app.assistant import history
from app.assistant.agent import AssistantService, AssistantUnavailable, build_assistant_service
from app.assistant.rate_limit import SlidingWindowLimiter, rate_limiter
from app.assistant.tools import ToolExecutor
from app.auth import Principal, require_permission
from app.clients.transactions_client import TransactionsClient
from app.config import settings
from app.database import get_db, get_session_factory
from app.metrics import ASSISTANT_CONVERSATIONS, ASSISTANT_MESSAGES, ASSISTANT_REQUEST_DURATION, ASSISTANT_TOKENS
from app.routers.analysis import _resolve_me

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assistant"])

MAX_MESSAGES = 20
MAX_CONTENT_CHARS = 2000


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_CONTENT_CHARS)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():  # a API recusa texto só com espaços (400), o que viraria 503
            raise ValueError("mensagem vazia")
        return value


class AssistantRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    rota: str | None = Field(default=None, max_length=300)
    conversation_id: str | None = Field(default=None, max_length=64)

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
    conversation_id: str
    message_id: int


class ConversationSummary(BaseModel):
    id: str
    titulo: str
    updated_at: str
    mensagens: int


class ConversationMessage(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    fontes: list[Fonte]
    acoes: list[Acao]
    sugestoes: list[str]
    created_at: str


class ConversationDetail(BaseModel):
    id: str
    titulo: str
    mensagens: list[ConversationMessage]


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


NOT_FOUND_BODY = {"error": "CONVERSA_NAO_ENCONTRADA"}
PERMISSAO_ASSISTENTE = "usar_assistente"
PERMISSAO_FINANCEIRO = "ver_financeiro"
SEM_PERMISSAO_BODY = {"error": "SEM_PERMISSAO", "permissao": PERMISSAO_ASSISTENTE}


def _lacks(me, permissao: str) -> bool:
    """Membro presente sem a permissão. `membro` nulo/ausente (Onboarding antigo, dono antes da migração) libera."""
    return me.membro is not None and not me.membro.can(permissao)


def require_assistant_access(principal: Principal = Depends(require_permission("read:self"))) -> Principal:
    """Histórico do assistente: exige `usar_assistente` quando o /me traz `membro`."""
    if _lacks(_resolve_me(principal), PERMISSAO_ASSISTENTE):
        raise AssistantPermissionDenied()
    return principal


class AssistantPermissionDenied(Exception):
    """Vira 403 `SEM_PERMISSAO_BODY` pelo handler de app.main."""
NOT_FOUND = {404: {"description": '`{"error":"CONVERSA_NAO_ENCONTRADA"}` (inexistente, de outro usuário ou apagada)'}}
FORBIDDEN_DESC = '`{"error":"SEM_PERMISSAO","permissao":"usar_assistente"}` ou `{"error":"MEMBRO_SUSPENSO"}`'
FORBIDDEN = {403: {"description": FORBIDDEN_DESC}}
ERRORS = {
    **NOT_FOUND,
    **FORBIDDEN,
    409: {"description": 'Onboarding não aprovado: `{"error":"ONBOARDING_PENDENTE","status":...}`'},
    429: {"description": '`{"error":"LIMITE_ASSISTENTE"}` (10 mensagens/min por usuário)'},
    503: {
        "description": '`{"error":"ASSISTENTE_INDISPONIVEL"}` (sem chave, falha ou prazo do LLM); depois que a '
        "pergunta foi gravada, traz também `conversation_id`"
    },
}


@router.post("/me/assistant/messages", response_model=AssistantResponse, responses=ERRORS)
def post_assistant_message(
    body: AssistantRequest,
    principal: Principal = Depends(require_permission("read:self")),
    service: AssistantService | None = Depends(get_assistant_service),
    limiter: SlidingWindowLimiter = Depends(get_rate_limiter),
    session_factory: Callable[[], Session] = Depends(get_session_factory),
):
    """Pergunta ao Assistente ErpIa sobre a empresa do cliente dono do token.

    Ferramentas só de leitura e simulação no Transactions (token repassado) e a análise do próprio Score; até 6
    chamadas de ferramenta e 45 s por resposta. Nunca executa pagamento, contratação ou escrita.

    Sem `conversation_id`, cria uma conversa (as mensagens anteriores do corpo viram o histórico dela); com ele, a
    conversa precisa ser do usuário e não estar apagada (senão 404), e o histórico vem do banco (20 últimas).
    """
    started = time.perf_counter()
    result, tool_calls, ran = "error", 0, False
    try:
        if service is None:
            result = "disabled"
            return _error(503, {"error": "ASSISTENTE_INDISPONIVEL"})
        me = _resolve_me(principal)
        if _lacks(me, PERMISSAO_ASSISTENTE):  # antes do rate limit e de gravar qualquer coisa
            result = "sem_permissao"
            return _error(403, SEM_PERMISSAO_BODY)
        allowed, retry_after = limiter.acquire(principal.subject)
        if not allowed:
            result = "limited"
            return _error(429, {"error": "LIMITE_ASSISTENTE"}, {"Retry-After": str(retry_after)})
        if me.customer_id is not None and me.onboarding_status != "APROVADO":
            me = _resolve_me(principal, fresh=True)  # o status em cache pode ser anterior à aprovação
        if me.customer_id is None or me.onboarding_status != "APROVADO":
            result = "pendente"
            return _error(409, {"error": "ONBOARDING_PENDENTE", "status": me.onboarding_status or "NAO_INICIADO"})

        customer_id = me.customer_id
        messages = [m.model_dump() for m in body.messages]
        try:
            with session_factory() as db:  # transação 1, fechada antes do LLM
                turn = history.start_turn(
                    db,
                    sub=principal.subject,
                    customer_id=customer_id,
                    conversation_id=body.conversation_id,
                    question=messages[-1]["content"],
                    seed=[] if body.conversation_id is not None else messages[:-1],
                    rota=body.rota,
                )
        except history.ConversationNotFound:
            result = "not_found"
            return _error(404, NOT_FOUND_BODY)
        if turn.created:
            ASSISTANT_CONVERSATIONS.inc()
        conversation_id = str(turn.conversation_id)

        def analysis_provider() -> dict | None:
            with session_factory() as db:  # sessão curta só para a ferramenta consultar_analise
                return latest_analysis_body(db, customer_id)

        tools = ToolExecutor(TransactionsClient(access_token=principal.token), analysis_provider=analysis_provider)
        ran = True
        llm_started = time.perf_counter()
        try:
            outcome = service.answer(
                turn.history, body.rota, tools, financeiro=not _lacks(me, PERMISSAO_FINANCEIRO)
            )
        except AssistantUnavailable as exc:
            result, tool_calls = exc.kind, exc.tool_calls
            _finish(session_factory, turn.conversation_id, None, "error", service.model,
                    exc.input_tokens, exc.output_tokens, llm_started)
            return _error(503, {"error": "ASSISTENTE_INDISPONIVEL", "conversation_id": conversation_id})
        result, tool_calls = outcome.result, outcome.tool_calls
        message_id = _finish(
            session_factory, turn.conversation_id, outcome.reply, "refused" if outcome.refused else "ok",
            outcome.model, outcome.input_tokens, outcome.output_tokens, llm_started,
        )
        return {**outcome.reply, "conversation_id": conversation_id, "message_id": message_id}
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


def _finish(session_factory, conversation_id, reply, result, model, input_tokens, output_tokens, llm_started) -> int:
    """Transação 2: só a resposta final, modelo, tokens somados no loop, latência do LLM e resultado."""
    ASSISTANT_TOKENS.labels(direction="input").inc(input_tokens)
    ASSISTANT_TOKENS.labels(direction="output").inc(output_tokens)
    latency_ms = int((time.perf_counter() - llm_started) * 1000)
    with session_factory() as db:
        return history.finish_turn(
            db,
            conversation_id,
            reply=reply,
            result=result,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )


# --- histórico ----------------------------------------------------------------------------------


@router.get("/me/assistant/conversations", response_model=list[ConversationSummary], responses=FORBIDDEN)
def list_conversations(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_assistant_access),
):
    """Conversas do usuário dono do token na empresa atual, não apagadas, da mais recente para a mais antiga."""
    customer_id = _resolve_me(principal).customer_id  # já em cache pelo require_assistant_access
    if customer_id is None:
        return []
    return history.list_conversations(db, principal.subject, limit, offset, customer_id=customer_id)


@router.get(
    "/me/assistant/conversations/{conversation_id}", response_model=ConversationDetail,
    responses={**NOT_FOUND, **FORBIDDEN},
)
def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_assistant_access),
):
    """Mensagens da conversa em ordem (pergunta e resposta final; nunca o conteúdo das ferramentas).

    Só conversas da empresa atual do usuário: quem saiu da empresa não relê os dados dela (404).
    """
    customer_id = _resolve_me(principal).customer_id  # já em cache pelo require_assistant_access
    if customer_id is None:
        return _error(404, NOT_FOUND_BODY)
    found = history.get_conversation(db, principal.subject, conversation_id, customer_id=customer_id)
    return found if found is not None else _error(404, NOT_FOUND_BODY)


@router.delete(
    "/me/assistant/conversations/{conversation_id}", status_code=204, response_class=Response,
    responses={**NOT_FOUND, **FORBIDDEN},
)
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_assistant_access),
):
    """Soft delete; a conversa some da API na hora e do banco depois da retenção (30 dias)."""
    if not history.soft_delete(db, principal.subject, conversation_id):
        return _error(404, NOT_FOUND_BODY)
    return Response(status_code=204)


@router.delete(
    "/me/assistant/conversations", status_code=204, response_class=Response, responses=FORBIDDEN
)
def delete_all_conversations(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_assistant_access),
):
    """Apaga (soft delete) todo o histórico do usuário (LGPD); idempotente."""
    history.soft_delete_all(db, principal.subject)
    return Response(status_code=204)
