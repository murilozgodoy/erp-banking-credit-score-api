"""Publisher RabbitMQ (aio-pika) dos eventos do Score (porta do publisher do Onboarding).

`RABBITMQ_URL` vazio = mensageria desligada (no-op, sem métrica). Falha de publicação nunca propaga:
é logada e contada em `events_published_total{result="error"}`.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import aio_pika

from app.metrics import EVENTS_PUBLISHED

logger = logging.getLogger(__name__)

EXCHANGE_NAME = "erp.events"
ANALYSIS_COMPLETED = "business.analysis_completed"


def envelope(event_type: str, event_id: str | None = None, **payload: Any) -> dict[str, Any]:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


def analysis_completed_event(
    customer_id: int, status: str, score, faixa_risco, gerado_por: str, source_event_id: str | None = None
) -> dict:
    """`event_id` derivado do evento de origem: a republicação após falha tem o mesmo id (idempotente)."""
    event_id = (
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"{ANALYSIS_COMPLETED}:{source_event_id}")) if source_event_id else None
    )
    return envelope(
        ANALYSIS_COMPLETED,
        event_id=event_id,
        customer_id=customer_id,
        status=status,
        score=score,
        faixa_risco=faixa_risco,
        gerado_por=gerado_por,
    )


class EventPublisher:
    def __init__(self, url: str) -> None:
        self.url = url
        self._connection = None
        self._channel = None
        self._exchange = None
        # Serializa connect/close: publishes concorrentes após uma falha não abrem várias conexões.
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def connect(self) -> None:
        """Conecta e declara a exchange; em falha loga e tenta de novo no próximo publish."""
        if not self.enabled or self._exchange is not None:
            return
        async with self._lock:
            if self._exchange is not None:  # double-checked: outra task já conectou
                return
            try:
                self._connection = await aio_pika.connect_robust(self.url)
                self._channel = await self._connection.channel()
                self._exchange = await self._channel.declare_exchange(
                    EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
                )
                logger.info("publisher conectado ao RabbitMQ (exchange %s)", EXCHANGE_NAME)
            except Exception:
                logger.exception("falha ao conectar o publisher no RabbitMQ; nova tentativa no próximo publish")
                await self._close_unlocked()

    async def close(self) -> None:
        """Fecha a conexão; idempotente."""
        async with self._lock:
            await self._close_unlocked()

    async def _close_unlocked(self) -> None:
        connection = self._connection
        self._connection = self._channel = self._exchange = None
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                logger.exception("falha ao fechar a conexão do publisher")

    async def publish(self, routing_key: str, payload: dict[str, Any]) -> bool:
        """Publica um evento JSON persistente. Nunca levanta exceção; `False` = não publicou."""
        if not self.enabled:
            return True
        try:
            await self.connect()
            if self._exchange is None:
                raise RuntimeError("RabbitMQ indisponível")
            message = aio_pika.Message(
                body=json.dumps(payload, default=str).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=payload.get("event_id"),
                type=payload.get("event_type"),
            )
            await self._exchange.publish(message, routing_key=routing_key)
        except Exception:
            logger.exception("falha ao publicar evento %s", routing_key)
            EVENTS_PUBLISHED.labels(routing_key=routing_key, result="error").inc()
            return False
        EVENTS_PUBLISHED.labels(routing_key=routing_key, result="ok").inc()
        return True
