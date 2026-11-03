import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

from app.messaging import consumer as consumer_mod
from app.messaging.consumer import Binding, EventConsumer, handle_message, process_body
from app.messaging.handlers import handle_credit_decided
from app.metrics import EVENTS_CONSUMED
from app.models import CreditDecisionEvent


def make_event(**overrides):
    event = {
        "event_id": str(uuid.uuid4()),
        "event_type": "credit.decided",
        "occurred_at": "2026-11-03T12:00:00Z",
        "credit_request_id": 10,
        "customer_id": 1,
        "valor_solicitado": 5000.0,
        "prazo_meses": 12,
        "score": 780.0,
        "faixa_risco": "BAIXO",
        "status": "APROVADO",
        "motivo": "Score suficiente",
        "taxa_juros": 2.99,
    }
    event.update(overrides)
    return event


BINDING = Binding("credit-score.credit-decided", "credit.decided", handle_credit_decided)


def counter(result):
    return EVENTS_CONSUMED.labels("credit.decided", result)._value.get()


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


# --- handler ---------------------------------------------------------------


def test_handler_persists_event(db_session):
    assert handle_credit_decided(db_session, make_event()) == "ok"
    row = db_session.query(CreditDecisionEvent).one()
    assert row.status == "APROVADO"
    assert row.taxa_juros == 2.99
    assert row.received_at is not None


def test_handler_is_idempotent(db_session):
    event = make_event()
    assert handle_credit_decided(db_session, event) == "ok"
    assert handle_credit_decided(db_session, event) == "duplicate"
    assert db_session.query(CreditDecisionEvent).count() == 1


def test_handler_accepts_denied_without_score(db_session):
    event = make_event(score=None, faixa_risco=None, status="NEGADO", taxa_juros=None)
    assert handle_credit_decided(db_session, event) == "ok"


def test_handler_rejects_invalid_payload(db_session):
    assert handle_credit_decided(db_session, {"event_id": "x"}) == "invalid"
    assert db_session.query(CreditDecisionEvent).count() == 0


# --- process_body / handle_message -----------------------------------------


def test_process_body_ok_increments_metric(session_factory):
    before = counter("ok")
    assert process_body(json.dumps(make_event()).encode(), BINDING, session_factory) == "ok"
    assert counter("ok") == before + 1


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]"])
def test_process_body_invalid_json(session_factory, body):
    before = counter("invalid")
    assert process_body(body, BINDING, session_factory) == "invalid"
    assert counter("invalid") == before + 1


def test_process_body_handler_error_rolls_back_and_counts(session_factory):
    def boom(db, payload):
        raise RuntimeError("db down")

    binding = Binding("q", "credit.decided", boom)
    before = counter("error")
    with pytest.raises(RuntimeError):
        process_body(b"{}", binding, session_factory)
    assert counter("error") == before + 1


def fake_message(body: bytes, redelivered=False):
    msg = MagicMock()
    msg.body = body
    msg.redelivered = redelivered
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()
    return msg


async def test_handle_message_acks_on_success(session_factory):
    msg = fake_message(json.dumps(make_event()).encode())
    await handle_message(msg, BINDING, session_factory)
    msg.ack.assert_awaited_once()
    msg.nack.assert_not_awaited()


async def test_handle_message_acks_invalid(session_factory):
    msg = fake_message(b"garbage")
    await handle_message(msg, BINDING, session_factory)
    msg.ack.assert_awaited_once()


@pytest.mark.parametrize("redelivered,requeue", [(False, True), (True, False)])
async def test_handle_message_nacks_on_error(session_factory, redelivered, requeue):
    binding = Binding("q", "credit.decided", MagicMock(side_effect=RuntimeError("x")))
    msg = fake_message(b"{}", redelivered=redelivered)
    await handle_message(msg, binding, session_factory)
    msg.nack.assert_awaited_once_with(requeue=requeue)
    msg.ack.assert_not_awaited()


# --- EventConsumer ---------------------------------------------------------


async def test_consumer_noop_without_url(session_factory):
    c = EventConsumer("", [BINDING], session_factory)
    c.start()
    assert c._task is None
    await c.stop()


async def test_consumer_declares_topology_and_consumes(session_factory):
    queue = AsyncMock()
    channel = AsyncMock()
    exchange = object()
    channel.declare_exchange.return_value = exchange
    channel.declare_queue.return_value = queue
    connection = AsyncMock()
    connection.channel.return_value = channel
    connection.__aenter__.return_value = connection

    with patch.object(consumer_mod.aio_pika, "connect_robust", AsyncMock(return_value=connection)):
        c = EventConsumer("amqp://guest:guest@localhost/", [BINDING], session_factory)
        c.start()
        for _ in range(50):
            await asyncio.sleep(0)
            if queue.consume.await_count:
                break
        await c.stop()

    channel.declare_exchange.assert_awaited_once()
    assert channel.declare_exchange.await_args.args[0] == "erp.events"
    channel.declare_queue.assert_awaited_once_with("credit-score.credit-decided", durable=True)
    queue.bind.assert_awaited_once_with(exchange, routing_key="credit.decided")
    callback = queue.consume.await_args.args[0]

    msg = fake_message(json.dumps(make_event()).encode())
    await callback(msg)
    msg.ack.assert_awaited_once()
    assert c._task is None


async def test_consumer_retries_after_failure(session_factory):
    connect = AsyncMock(side_effect=ConnectionError("broker down"))
    with patch.object(consumer_mod.aio_pika, "connect_robust", connect):
        c = EventConsumer("amqp://x/", [BINDING], session_factory, retry_delay=0)
        c.start()
        for _ in range(50):
            await asyncio.sleep(0)
            if connect.await_count >= 2:
                break
        await c.stop()
    assert connect.await_count >= 2


# --- GET /scores/decisions -------------------------------------------------


def test_list_decisions_most_recent_first(client, db_session):
    handle_credit_decided(
        db_session, make_event(occurred_at="2026-11-01T10:00:00Z", status="NEGADO")
    )
    handle_credit_decided(db_session, make_event(occurred_at="2026-11-03T10:00:00Z"))
    handle_credit_decided(db_session, make_event(customer_id=2))

    r = client.get("/scores/decisions", params={"customer_id": 1})
    assert r.status_code == 200
    body = r.json()
    assert [d["status"] for d in body] == ["APROVADO", "NEGADO"]
    assert set(body[0]) == {
        "id", "event_id", "credit_request_id", "customer_id", "status", "motivo",
        "valor_solicitado", "prazo_meses", "score", "faixa_risco", "taxa_juros",
        "occurred_at", "received_at",
    }


def test_list_decisions_empty(client):
    r = client.get("/scores/decisions", params={"customer_id": 5})
    assert r.status_code == 200
    assert r.json() == []


def test_list_decisions_requires_customer_id(client):
    assert client.get("/scores/decisions").status_code == 422
