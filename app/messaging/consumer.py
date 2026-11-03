"""Consumer RabbitMQ (aio-pika) rodando como task em background no lifespan.

Sem `RABBITMQ_URL` o consumer não é iniciado (no-op), então dev e testes rodam sem broker.
"""
import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import aio_pika
from sqlalchemy.orm import Session

from app.metrics import EVENTS_CONSUMED

logger = logging.getLogger(__name__)

EXCHANGE = "erp.events"


@dataclass
class HandlerOutcome:
    """Resultado de um handler que, além de gravar, quer publicar eventos.

    Os handlers rodam síncronos numa thread e fazem o commit; os `events` (routing_key, payload)
    são publicados depois, na camada assíncrona, antes do ack da mensagem.
    """

    result: str
    events: list[tuple[str, dict]] = field(default_factory=list)


@dataclass(frozen=True)
class Binding:
    queue: str
    routing_key: str
    handler: Callable[[Session, dict], "str | HandlerOutcome"]


def process_body(
    body: bytes,
    binding: Binding,
    session_factory: Callable[[], Session],
    outbox: list | None = None,
) -> str:
    """Decodifica o JSON, chama o handler numa sessão própria e registra a métrica.

    Eventos devolvidos pelo handler (`HandlerOutcome`) vão para `outbox`.
    """
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("payload não é um objeto JSON")
    except ValueError:
        logger.warning("mensagem %s com JSON inválido", binding.routing_key)
        result = "invalid"
    else:
        db = session_factory()
        try:
            result = binding.handler(db, payload)
            if isinstance(result, HandlerOutcome):
                if outbox is not None:
                    outbox.extend(result.events)
                result = result.result
        except Exception:
            db.rollback()
            EVENTS_CONSUMED.labels(binding.routing_key, "error").inc()
            raise
        finally:
            db.close()
    EVENTS_CONSUMED.labels(binding.routing_key, result).inc()
    return result


async def handle_message(
    message, binding: Binding, session_factory: Callable[[], Session], publisher=None
) -> None:
    """Ack no sucesso; erro inesperado volta para a fila uma vez e depois é descartado.

    Eventos gerados pelo handler são publicados após o commit e antes do ack (o publisher nunca levanta).
    Se a publicação falhar, a mensagem volta para a fila: a reentrega cai como duplicata e o handler
    devolve o mesmo evento para republicar.
    """
    outbox: list[tuple[str, dict]] = []
    try:
        await asyncio.to_thread(process_body, message.body, binding, session_factory, outbox)
    except Exception:
        logger.exception("falha ao processar %s", binding.routing_key)
        await message.nack(requeue=not message.redelivered)
        return
    if publisher is not None:
        for routing_key, event in outbox:
            if not await publisher.publish(routing_key, event):
                await message.nack(requeue=True)
                return
    await message.ack()


class EventConsumer:
    def __init__(
        self,
        url: str,
        bindings: list[Binding],
        session_factory: Callable[[], Session],
        retry_delay: float = 5.0,
        publisher=None,
    ):
        self.url = url
        self.publisher = publisher
        self.bindings = bindings
        self.session_factory = session_factory
        self.retry_delay = retry_delay
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if not self.url:
            logger.info("RABBITMQ_URL vazio: consumer desligado")
            return
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_forever(self) -> None:
        while True:
            try:
                await self._consume()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("consumer RabbitMQ caiu; nova tentativa em %ss", self.retry_delay)
            await asyncio.sleep(self.retry_delay)

    async def _consume(self) -> None:
        connection = await aio_pika.connect_robust(self.url)
        async with connection:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=10)
            exchange = await channel.declare_exchange(
                EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
            )
            for binding in self.bindings:
                queue = await channel.declare_queue(binding.queue, durable=True)
                await queue.bind(exchange, routing_key=binding.routing_key)
                await queue.consume(self._callback(binding))
                logger.info("consumindo %s na fila %s", binding.routing_key, binding.queue)
            await asyncio.Future()  # mantém a conexão aberta até o cancelamento

    def _callback(self, binding: Binding):
        async def callback(message) -> None:
            await handle_message(message, binding, self.session_factory, self.publisher)

        return callback
