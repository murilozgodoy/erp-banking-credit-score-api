"""Histórico do Assistente ErpIa no banco (contrato: seção 10 do PORTAL_CLIENTE.md).

Cada função recebe uma sessão curta e faz no máximo uma transação: o pool do Postgres gerenciado é pequeno, então a
rota nunca segura uma conexão durante a chamada ao LLM. O dono de uma conversa é sempre o `sub` do token.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.models import AssistantConversation, AssistantMessage

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 20
TITULO_MAX = 80
ELLIPSIS = "…"

# a linha de auditoria de um 503 (assistente, result=error, content vazio) não aparece na API nem no histórico
VISIBLE = or_(AssistantMessage.result.is_(None), AssistantMessage.result != "error")


class ConversationNotFound(Exception):
    """Conversa inexistente, de outro `sub` ou apagada: vira 404 CONVERSA_NAO_ENCONTRADA."""


@dataclass
class TurnStart:
    conversation_id: uuid.UUID
    history: list[dict]  # até 20 mensagens anteriores + a pergunta nova, no formato {role, content}
    created: bool


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    """ISO 8601 em UTC com `Z` (o SQLite devolve datas sem fuso; tudo é gravado em UTC)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_titulo(question: str) -> str:
    """Título da conversa a partir da 1ª pergunta: espaços normalizados, ≤ 80 caracteres, cortado na palavra."""
    text = " ".join(question.split())
    if len(text) <= TITULO_MAX:
        return text
    cut = text[: TITULO_MAX - len(ELLIPSIS) + 1]
    space = cut.rfind(" ")
    cut = cut[:space] if space > 0 else cut[: TITULO_MAX - len(ELLIPSIS)]
    return cut[: TITULO_MAX - len(ELLIPSIS)].rstrip(" .,;:!?-") + ELLIPSIS


def parse_id(raw: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None


def _owned(
    db: Session, sub: str, conversation_id: uuid.UUID | None, customer_id: int | None = None
) -> AssistantConversation | None:
    """Conversa do `sub`, não apagada. Com `customer_id`, também da empresa atual do usuário: quem saiu da empresa
    (ou entrou em outra) não lê nem continua conversas com os dados da empresa anterior."""
    if conversation_id is None:
        return None
    query = select(AssistantConversation).where(
        AssistantConversation.id == conversation_id,
        AssistantConversation.auth0_sub == sub,
        AssistantConversation.deleted_at.is_(None),
    )
    if customer_id is not None:
        query = query.where(AssistantConversation.customer_id == customer_id)
    return db.scalars(query).first()


def _message(conversation_id: uuid.UUID, role: str, content: str, now: datetime, **extra) -> AssistantMessage:
    return AssistantMessage(conversation_id=conversation_id, role=role, content=content, created_at=now, **extra)


def start_turn(
    db: Session,
    *,
    sub: str,
    customer_id: int,
    conversation_id: str | None,
    question: str,
    seed: list[dict],
    rota: str | None,
    now: Callable[[], datetime] = utcnow,
) -> TurnStart:
    """Transação 1: valida ou cria a conversa, grava a pergunta e lê o histórico. Faz commit.

    Sem `conversation_id`, as mensagens anteriores do corpo (`seed`, o front antigo) entram como histórico da conversa
    nova; com ele, o histórico vem só do banco.
    """
    ts = now()
    created = conversation_id is None
    if created:
        seed = _drop_leading_assistant(seed)
        first_question = next((m["content"] for m in seed if m["role"] == "user"), question)
        conversation = AssistantConversation(
            id=uuid.uuid4(),
            customer_id=customer_id,
            auth0_sub=sub,
            titulo=make_titulo(first_question),
            rota_inicial=rota,
            created_at=ts,
            updated_at=ts,
        )
        db.add(conversation)
        db.flush()
        for m in seed:
            extra = {"fontes": [], "acoes": [], "sugestoes": []} if m["role"] == "assistant" else {}
            db.add(_message(conversation.id, m["role"], m["content"], ts, **extra))
        db.flush()
    else:
        conversation = _owned(db, sub, parse_id(conversation_id), customer_id)
        if conversation is None:
            raise ConversationNotFound
        conversation.updated_at = ts

    rows = db.scalars(
        select(AssistantMessage)
        .where(AssistantMessage.conversation_id == conversation.id, VISIBLE)
        .order_by(AssistantMessage.id.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    history = [{"role": r.role, "content": r.content} for r in reversed(rows)]
    db.add(_message(conversation.id, "user", question, ts))
    conversation_uuid = conversation.id
    db.commit()
    return TurnStart(conversation_uuid, history + [{"role": "user", "content": question}], created)


def _drop_leading_assistant(seed: list[dict]) -> list[dict]:
    """A saudação do assistente antes da 1ª pergunta não é histórico (o modelo também a descarta)."""
    for i, m in enumerate(seed):
        if m["role"] == "user":
            return seed[i:]
    return []


def finish_turn(
    db: Session,
    conversation_id: uuid.UUID,
    *,
    reply: dict | None,
    result: str,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    now: Callable[[], datetime] = utcnow,
) -> int:
    """Transação 2: grava só a resposta final (ou a linha de auditoria de erro) e toca `updated_at`. Faz commit."""
    ts = now()
    reply = reply or {}
    row = _message(
        conversation_id,
        "assistant",
        reply.get("resposta", ""),
        ts,
        fontes=reply.get("fontes", []),
        acoes=reply.get("acoes", []),
        sugestoes=reply.get("sugestoes", []),
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        result=result,
    )
    db.add(row)
    db.execute(
        update(AssistantConversation).where(AssistantConversation.id == conversation_id).values(updated_at=ts)
    )
    db.commit()
    return row.id


# --- leitura e exclusão ----------------------------------------------------------------------


def list_conversations(
    db: Session, sub: str, limit: int, offset: int, customer_id: int | None = None
) -> list[dict]:
    count = (
        select(func.count(AssistantMessage.id))
        .where(AssistantMessage.conversation_id == AssistantConversation.id, VISIBLE)
        .correlate(AssistantConversation)
        .scalar_subquery()
    )
    query = select(AssistantConversation, count).where(
        AssistantConversation.auth0_sub == sub, AssistantConversation.deleted_at.is_(None)
    )
    if customer_id is not None:
        query = query.where(AssistantConversation.customer_id == customer_id)
    rows = db.execute(
        query
        .order_by(AssistantConversation.updated_at.desc(), AssistantConversation.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        {"id": str(c.id), "titulo": c.titulo, "updated_at": iso(c.updated_at), "mensagens": n} for c, n in rows
    ]


def get_conversation(db: Session, sub: str, conversation_id: str, customer_id: int | None = None) -> dict | None:
    conversation = _owned(db, sub, parse_id(conversation_id), customer_id)
    if conversation is None:
        return None
    rows = db.scalars(
        select(AssistantMessage)
        .where(AssistantMessage.conversation_id == conversation.id, VISIBLE)
        .order_by(AssistantMessage.id)
    ).all()
    return {
        "id": str(conversation.id),
        "titulo": conversation.titulo,
        "mensagens": [
            {
                "id": r.id,
                "role": r.role,
                "content": r.content,
                "fontes": r.fontes or [],
                "acoes": r.acoes or [],
                "sugestoes": r.sugestoes or [],
                "created_at": iso(r.created_at),
            }
            for r in rows
        ],
    }


def soft_delete(db: Session, sub: str, conversation_id: str, now: Callable[[], datetime] = utcnow) -> bool:
    conversation = _owned(db, sub, parse_id(conversation_id))
    if conversation is None:
        return False
    conversation.deleted_at = now()
    db.commit()
    return True


def soft_delete_all(db: Session, sub: str, now: Callable[[], datetime] = utcnow) -> int:
    result = db.execute(
        update(AssistantConversation)
        .where(AssistantConversation.auth0_sub == sub, AssistantConversation.deleted_at.is_(None))
        .values(deleted_at=now())
    )
    db.commit()
    return result.rowcount or 0


# --- retenção --------------------------------------------------------------------------------


def purge_deleted(
    session_factory: Callable[[], Session],
    retention_days: int,
    now: Callable[[], datetime] = utcnow,
) -> int:
    """Apaga de vez (mensagens primeiro) as conversas com soft delete há mais de `retention_days` dias."""
    cutoff = now() - timedelta(days=retention_days)
    with session_factory() as db:
        ids = db.scalars(
            select(AssistantConversation.id).where(
                AssistantConversation.deleted_at.is_not(None), AssistantConversation.deleted_at < cutoff
            )
        ).all()
        if not ids:
            return 0
        db.execute(delete(AssistantMessage).where(AssistantMessage.conversation_id.in_(ids)))
        db.execute(delete(AssistantConversation).where(AssistantConversation.id.in_(ids)))
        db.commit()
    logger.info("assistente: %d conversas apagadas removidas de vez", len(ids))
    return len(ids)


async def purge_loop(
    session_factory: Callable[[], Session],
    retention_days: int,
    interval_hours: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Job do lifespan: purga no startup e a cada `interval_hours`, numa thread com sessão própria e curta."""
    while True:
        try:
            await asyncio.to_thread(purge_deleted, session_factory, retention_days)
        except Exception as exc:  # noqa: BLE001 — o job nunca derruba a API
            logger.warning("assistente: falha na purga do histórico: %s", type(exc).__name__)
        await sleep(max(interval_hours, 0.01) * 3600)
