import json
import logging
import sys
import uuid

import httpx
import respx

from app.config import settings
from app.logging_config import JsonFormatter, configure_logging, request_id_var
from app.messaging.handlers import handle_anamnesis_updated
from app.models import CustomerScoreStatus


def anamnesis_event(**overrides):
    event = {
        "event_id": str(uuid.uuid4()),
        "event_type": "customer.anamnesis_updated",
        "occurred_at": "2026-11-03T12:00:00Z",
        "customer_id": 1,
        "dividas_ativas": 3000.0,
        "comprometimento_renda": 45.0,
    }
    event.update(overrides)
    return event


# --- C8: customer.anamnesis_updated ----------------------------------------


def test_anamnesis_marks_score_as_stale(db_session):
    assert handle_anamnesis_updated(db_session, anamnesis_event()) == "ok"
    row = db_session.get(CustomerScoreStatus, 1)
    assert row.desatualizado is True
    assert row.comprometimento_renda == 45.0


def test_anamnesis_duplicate_and_update(db_session):
    event = anamnesis_event()
    assert handle_anamnesis_updated(db_session, event) == "ok"
    assert handle_anamnesis_updated(db_session, event) == "duplicate"
    assert handle_anamnesis_updated(db_session, anamnesis_event(dividas_ativas=10.0)) == "ok"
    assert db_session.get(CustomerScoreStatus, 1).dividas_ativas == 10.0


def test_anamnesis_invalid_payload(db_session):
    assert handle_anamnesis_updated(db_session, {"customer_id": "x"}) == "invalid"


def test_status_endpoint_without_events(client):
    r = client.get("/scores/customers/7/status")
    assert r.status_code == 200
    assert r.json()["desatualizado"] is False


@respx.mock
def test_status_stale_until_new_evaluation(client, db_session, onboarding_response):
    handle_anamnesis_updated(db_session, anamnesis_event())
    body = client.get("/scores/customers/1/status").json()
    assert body["desatualizado"] is True
    assert body["motivo"]
    assert body["dividas_ativas"] == 3000.0

    respx.get(f"{settings.onboarding_api_url}/customers/1").mock(
        return_value=httpx.Response(200, json=onboarding_response)
    )
    assert client.post("/scores/evaluations", json={"customer_id": 1}).status_code == 201
    body = client.get("/scores/customers/1/status").json()
    assert body["desatualizado"] is False
    assert body["motivo"] is None


# --- C9: logs JSON + X-Request-ID ------------------------------------------


def _access(caplog):
    return [r for r in caplog.records if r.name == "app.access"]


def test_request_id_generated_and_logged(client, caplog):
    caplog.set_level(logging.INFO, logger="app.access")
    r = client.get("/health")
    uuid.UUID(r.headers["X-Request-ID"])
    line = json.loads(JsonFormatter().format(_access(caplog)[-1]))
    assert line["method"] == "GET"
    assert line["path"] == "/health"
    assert line["status"] == 200
    assert line["duration_ms"] >= 0


def test_incoming_request_id_echoed(client, caplog):
    caplog.set_level(logging.INFO, logger="app.access")
    r = client.get("/scores/customers/999/latest", headers={"X-Request-ID": "gw-1"})
    assert r.status_code == 404
    assert r.headers["X-Request-ID"] == "gw-1"
    assert _access(caplog)[-1].status == 404


def test_formatter_with_request_id_and_exception():
    token = request_id_var.set("abc")
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.getLogger("t").makeRecord(
                "t", logging.ERROR, __file__, 1, "falhou %s", ("x",), sys.exc_info()
            )
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert line["request_id"] == "abc"
    assert line["message"] == "falhou x"
    assert "ValueError: boom" in line["exc_info"]


def test_configure_logging_idempotent():
    configure_logging("info")
    configure_logging("DEBUG")
    root = logging.getLogger()
    assert len([h for h in root.handlers if getattr(h, "_erp_json", False)]) == 1
    assert root.level == logging.DEBUG
    configure_logging("INFO")
