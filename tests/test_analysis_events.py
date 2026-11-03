import asyncio
import copy
import json
from functools import partial
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import main as main_mod
from app.analysis.repository import BusinessAnalysisRepository
from app.messaging import publisher as publisher_mod
from app.messaging.consumer import Binding, HandlerOutcome, handle_message, process_body
from app.messaging.handlers import handle_onboarding_reviewed, handle_onboarding_submitted
from app.messaging.publisher import EventPublisher, analysis_completed_event
from app.metrics import EVENTS_CONSUMED, EVENTS_PUBLISHED


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def reviewed(**overrides):
    event = {
        "event_id": "evt-rev-1",
        "event_type": "customer.onboarding_reviewed",
        "occurred_at": "2026-11-03T13:00:00Z",
        "customer_id": 7,
        "decisao": "APROVADO",
        "comentario": "Documentação conferida",
        "reviewer": "analista@erpia",
    }
    event.update(overrides)
    return event


class FakeLlm:
    def analyze(self, payload, rules_result):
        return {"enquadramento": "ESPECIFICO", "confianca": 0.9, "resumo": "Atípico.",
                "pontos_fortes": [], "riscos": [], "insights": []}


# --- onboarding_submitted -----------------------------------------------------


def test_submitted_persists_and_returns_event(db_session, business_payload):
    out = handle_onboarding_submitted(db_session, business_payload)
    assert isinstance(out, HandlerOutcome) and out.result == "ok"
    (routing_key, event), = out.events
    assert routing_key == "business.analysis_completed"
    assert set(event) == {
        "event_id", "event_type", "occurred_at", "customer_id", "status", "score", "faixa_risco", "gerado_por",
    }
    assert event["event_type"] == "business.analysis_completed"
    assert event["customer_id"] == 7 and event["status"] == "APROVADO" and event["gerado_por"] == "regras"
    row = BusinessAnalysisRepository(db_session).latest(7)
    assert row.version == 1 and row.source_event_id == "evt-sub-1" and row.score == event["score"]
    assert row.resultado["kpis"]


def test_submitted_is_idempotent(db_session, business_payload):
    handle_onboarding_submitted(db_session, business_payload)
    assert handle_onboarding_submitted(db_session, business_payload) == "duplicate"
    assert BusinessAnalysisRepository(db_session).latest(7).version == 1


def test_resubmission_creates_new_version(db_session, business_payload):
    handle_onboarding_submitted(db_session, business_payload)
    again = copy.deepcopy(business_payload)
    again["event_id"] = "evt-sub-2"
    handle_onboarding_submitted(db_session, again)
    assert BusinessAnalysisRepository(db_session).latest(7).version == 2


def test_submitted_uses_llm(db_session, business_payload):
    out = handle_onboarding_submitted(db_session, business_payload, llm=FakeLlm())
    _, event = out.events[0]
    assert event["status"] == "ANALISE_MANUAL" and event["gerado_por"] == "llm+regras"


@pytest.mark.parametrize("mutate", [
    lambda p: p.pop("customer_id"),
    lambda p: p.pop("segmento"),
    lambda p: p.update(respostas="x"),
])
def test_submitted_invalid(db_session, business_payload, mutate):
    mutate(business_payload)
    assert handle_onboarding_submitted(db_session, business_payload) == "invalid"


def test_submitted_race_is_duplicate(business_payload):
    db = MagicMock()
    db.scalar.return_value = None
    db.commit.side_effect = IntegrityError("x", {}, Exception())
    assert handle_onboarding_submitted(db, business_payload) == "duplicate"
    db.rollback.assert_called_once()


# --- onboarding_reviewed -------------------------------------------------------


def test_reviewed_records_new_version(db_session, business_payload):
    handle_onboarding_submitted(db_session, business_payload, llm=FakeLlm())
    assert handle_onboarding_reviewed(db_session, reviewed(decisao="REPROVADO")) == "ok"
    row = BusinessAnalysisRepository(db_session).latest(7)
    assert (row.version, row.status, row.reviewer, row.review_comment) == (
        2, "REPROVADO", "analista@erpia", "Documentação conferida"
    )
    assert row.resultado["status"] == "REPROVADO"
    assert row.gerado_por == "llm+regras" and row.resultado["kpis"]
    assert handle_onboarding_reviewed(db_session, reviewed(decisao="REPROVADO")) == "duplicate"


def test_reviewed_visible_on_routes(client, db_session, business_payload):
    handle_onboarding_submitted(db_session, business_payload)
    handle_onboarding_reviewed(db_session, reviewed())
    body = client.get("/scores/analyses/7").json()
    assert body["status"] == "APROVADO" and body["score"] is not None


def test_reviewed_without_analysis(db_session):
    assert handle_onboarding_reviewed(db_session, reviewed()) == "invalid"


@pytest.mark.parametrize("override", [{"decisao": "TALVEZ"}, {"customer_id": "abc"}])
def test_reviewed_invalid(db_session, override):
    assert handle_onboarding_reviewed(db_session, reviewed(**override)) == "invalid"


def test_reviewed_race_is_duplicate(db_session, business_payload):
    handle_onboarding_submitted(db_session, business_payload)
    with patch.object(db_session, "commit", side_effect=IntegrityError("x", {}, Exception())):
        assert handle_onboarding_reviewed(db_session, reviewed()) == "duplicate"


# --- consumer + publisher ------------------------------------------------------


def fake_message(body: bytes):
    msg = MagicMock()
    msg.body = body
    msg.redelivered = False
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()
    return msg


SUBMITTED = Binding("credit-score.onboarding-submitted", "customer.onboarding_submitted", handle_onboarding_submitted)


def test_process_body_collects_outbox(session_factory, business_payload):
    outbox = []
    before = EVENTS_CONSUMED.labels("customer.onboarding_submitted", "ok")._value.get()
    assert process_body(json.dumps(business_payload).encode(), SUBMITTED, session_factory, outbox) == "ok"
    assert [rk for rk, _ in outbox] == ["business.analysis_completed"]
    assert EVENTS_CONSUMED.labels("customer.onboarding_submitted", "ok")._value.get() == before + 1
    # sem outbox o evento é simplesmente ignorado
    payload = {**business_payload, "event_id": "evt-x"}
    assert process_body(json.dumps(payload).encode(), SUBMITTED, session_factory) == "ok"


async def test_handle_message_publishes_after_commit_then_acks(session_factory, business_payload):
    publisher = MagicMock()
    order = []
    publisher.publish = AsyncMock(side_effect=lambda rk, ev: order.append(("publish", rk, ev["status"])))
    msg = fake_message(json.dumps(business_payload).encode())
    msg.ack = AsyncMock(side_effect=lambda: order.append(("ack",)))
    await handle_message(msg, SUBMITTED, session_factory, publisher)
    assert order == [("publish", "business.analysis_completed", "APROVADO"), ("ack",)]
    # duplicata: não publica de novo, mas faz ack
    publisher.publish.reset_mock()
    msg2 = fake_message(json.dumps(business_payload).encode())
    await handle_message(msg2, SUBMITTED, session_factory, publisher)
    publisher.publish.assert_not_awaited()
    msg2.ack.assert_awaited_once()


def test_analysis_completed_event_envelope():
    ev = analysis_completed_event(1, "APROVADO", 800, "BAIXO", "regras")
    assert ev["event_type"] == "business.analysis_completed" and ev["event_id"] and ev["occurred_at"]


def published(result):
    return EVENTS_PUBLISHED.labels("business.analysis_completed", result)._value.get()


async def test_publisher_disabled_is_noop():
    p = EventPublisher("")
    with patch.object(publisher_mod.aio_pika, "connect_robust", AsyncMock()) as connect:
        await p.publish("business.analysis_completed", {"event_id": "1"})
        connect.assert_not_awaited()
    await p.close()


def _connection(exchange):
    channel = AsyncMock()
    channel.declare_exchange.return_value = exchange
    connection = AsyncMock()
    connection.channel.return_value = channel
    return connection


async def test_publisher_publishes_persistent_message():
    exchange = AsyncMock()
    connection = _connection(exchange)
    before = published("ok")
    with patch.object(publisher_mod.aio_pika, "connect_robust", AsyncMock(return_value=connection)) as connect:
        p = EventPublisher("amqp://x/")
        ev = analysis_completed_event(1, "APROVADO", 800, "BAIXO", "regras")
        await p.publish("business.analysis_completed", ev)
        await p.publish("business.analysis_completed", ev)
        assert connect.await_count == 1  # conexão reaproveitada
    message = exchange.publish.await_args.args[0]
    assert exchange.publish.await_args.kwargs["routing_key"] == "business.analysis_completed"
    assert json.loads(message.body)["customer_id"] == 1
    assert message.message_id == ev["event_id"] and message.type == "business.analysis_completed"
    assert published("ok") == before + 2
    await p.close()
    connection.close.assert_awaited_once()
    await p.close()  # idempotente


async def test_publisher_connect_failure_counts_error_and_retries():
    before = published("error")
    connect = AsyncMock(side_effect=ConnectionError("down"))
    with patch.object(publisher_mod.aio_pika, "connect_robust", connect):
        p = EventPublisher("amqp://x/")
        await p.publish("business.analysis_completed", {"event_id": "1"})
        await p.publish("business.analysis_completed", {"event_id": "2"})
    assert connect.await_count == 2
    assert published("error") == before + 2


async def test_publisher_publish_failure_and_close_failure_are_swallowed():
    exchange = AsyncMock()
    exchange.publish.side_effect = RuntimeError("channel closed")
    connection = _connection(exchange)
    connection.close.side_effect = RuntimeError("already closed")
    before = published("error")
    with patch.object(publisher_mod.aio_pika, "connect_robust", AsyncMock(return_value=connection)):
        p = EventPublisher("amqp://x/")
        await p.publish("business.analysis_completed", {"event_id": "1"})
        await p.close()
    assert published("error") == before + 1


async def test_publisher_concurrent_connect_opens_one_connection():
    connection = _connection(AsyncMock())

    async def slow_connect(url):
        await asyncio.sleep(0)
        return connection

    connect = AsyncMock(side_effect=slow_connect)
    with patch.object(publisher_mod.aio_pika, "connect_robust", connect):
        p = EventPublisher("amqp://x/")
        await asyncio.gather(p.connect(), p.connect(), p.connect())
    assert connect.await_count == 1


# --- wiring ----------------------------------------------------------------------


def test_bindings_include_onboarding_queues():
    bindings = {b.routing_key: b for b in main_mod.build_bindings(llm="LLM")}
    sub = bindings["customer.onboarding_submitted"]
    assert sub.queue == "credit-score.onboarding-submitted"
    assert isinstance(sub.handler, partial) and sub.handler.keywords == {"llm": "LLM"}
    assert bindings["customer.onboarding_reviewed"].queue == "credit-score.onboarding-reviewed"
    assert {"credit.decided", "customer.anamnesis_updated"} <= set(bindings)


def test_build_consumer_wires_publisher_and_llm():
    publisher = EventPublisher("")
    with patch.object(main_mod, "build_llm_analyzer", return_value=None) as build:
        consumer = main_mod.build_consumer(publisher)
    build.assert_called_once()
    assert consumer.publisher is publisher
    assert len(consumer.bindings) == 4


def test_metric_series_zero_initialised():
    from prometheus_client import generate_latest

    text = generate_latest().decode()
    for rk in ("customer.onboarding_submitted", "customer.onboarding_reviewed"):
        assert f'events_consumed_total{{result="duplicate",routing_key="{rk}"}}' in text
    assert 'events_published_total{result="error",routing_key="business.analysis_completed"}' in text
