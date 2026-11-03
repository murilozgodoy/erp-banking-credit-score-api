"""Histórico do Assistente ErpIa (seção 10): conversas no banco, sessões curtas, retenção e métricas."""
import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import respx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.assistant import history
from app.assistant.agent import REFUSAL_TEXT
from app.auth import Principal, require_auth
from app.database import Base, get_session_factory
from app.main import app
from app.metrics import ASSISTANT_CONVERSATIONS, ASSISTANT_MESSAGES, ASSISTANT_TOKENS
from app.models import AssistantConversation, AssistantMessage
from tests.test_assistant_api import (  # noqa: F401 — fixtures `use` e `limiter` reaproveitados
    OVERVIEW,
    TX,
    URL,
    final,
    limiter,
    me_ok,
    message,
    text,
    tool_use,
    use,
)

SUB = "auth0|test"
CONVERSATIONS = "/me/assistant/conversations"


# --- helpers ---------------------------------------------------------------------------------


def usage(msg, input_tokens, output_tokens, model=None):
    msg.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    if model:
        msg.model = model
    return msg


def post(client, content, conversation_id=None, history_=None, rota="/app/recebiveis"):
    body = {"messages": (history_ or []) + [{"role": "user", "content": content}], "rota": rota}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    return client.post(URL, json=body)


def all_rows(engine, model):
    with Session(engine) as s:
        return s.scalars(select(model).order_by(model.created_at)).all() if model is AssistantConversation else \
            s.scalars(select(model).order_by(model.id)).all()


def make_conversation(engine, sub=SUB, n_messages=0, deleted_at=None, updated_at=None, titulo="Conversa"):
    ts = updated_at or datetime.now(timezone.utc)
    with Session(engine) as s:
        conv = AssistantConversation(
            id=uuid.uuid4(), customer_id=7, auth0_sub=sub, titulo=titulo, rota_inicial="/app",
            created_at=ts, updated_at=ts, deleted_at=deleted_at,
        )
        s.add(conv)
        s.flush()
        for i in range(n_messages):
            role = "user" if i % 2 == 0 else "assistant"
            extra = {"fontes": [], "acoes": [], "sugestoes": [], "result": "ok"} if role == "assistant" else {}
            s.add(AssistantMessage(conversation_id=conv.id, role=role, content=f"m{i}", created_at=ts, **extra))
        s.commit()
        return str(conv.id)


def counter(metric, **labels):
    return (metric.labels(**labels) if labels else metric)._value.get()


def as_user(sub, permissions=frozenset({"read:self"})):
    app.dependency_overrides[require_auth] = lambda: Principal("tok-" + sub, sub, permissions)


# --- criação e continuação -------------------------------------------------------------------


@respx.mock
def test_first_message_creates_conversation(client, use, engine):
    me_ok()
    calls = use([final(fontes=[])])
    before = counter(ASSISTANT_CONVERSATIONS)

    r = post(client, "  Quanto   tenho a receber nos próximos 90 dias?  ")

    assert r.status_code == 200
    body = r.json()
    assert uuid.UUID(body["conversation_id"])
    assert isinstance(body["message_id"], int)
    assert body["resposta"].startswith("Você tem")
    [conv] = all_rows(engine, AssistantConversation)
    assert str(conv.id) == body["conversation_id"]
    assert conv.auth0_sub == SUB and conv.customer_id == 7 and conv.deleted_at is None
    assert conv.titulo == "Quanto tenho a receber nos próximos 90 dias?"
    assert conv.rota_inicial == "/app/recebiveis"
    user, assistant = all_rows(engine, AssistantMessage)
    assert (user.role, user.result, user.model) == ("user", None, None)
    assert user.content == "  Quanto   tenho a receber nos próximos 90 dias?  "
    assert assistant.id == body["message_id"]
    assert assistant.role == "assistant" and assistant.result == "ok"
    assert assistant.content == body["resposta"]
    assert assistant.sugestoes == body["sugestoes"] and assistant.acoes == body["acoes"]
    assert assistant.fontes == []
    assert counter(ASSISTANT_CONVERSATIONS) == before + 1
    assert len(calls.calls) == 1


@respx.mock
def test_continue_uses_history_from_db(client, use, engine):
    me_ok()
    calls = use([final(resposta="Primeira resposta.", fontes=[]), final(resposta="Segunda.", fontes=[])])
    first = post(client, "Pergunta 1").json()
    before = counter(ASSISTANT_CONVERSATIONS)

    # o corpo traz um histórico qualquer: com conversation_id ele é ignorado, só a última mensagem vale
    r = post(client, "Pergunta 2", first["conversation_id"],
             history_=[{"role": "user", "content": "ignorada"}, {"role": "assistant", "content": "ignorada"}])

    assert r.status_code == 200
    assert r.json()["conversation_id"] == first["conversation_id"]
    assert r.json()["message_id"] > first["message_id"]
    assert calls.calls[1]["messages"] == [
        {"role": "user", "content": "Pergunta 1"},
        {"role": "assistant", "content": "Primeira resposta."},
        {"role": "user", "content": "Pergunta 2"},
    ]
    assert counter(ASSISTANT_CONVERSATIONS) == before  # não cria outra
    assert len(all_rows(engine, AssistantConversation)) == 1
    assert [m.content for m in all_rows(engine, AssistantMessage)] == [
        "Pergunta 1", "Primeira resposta.", "Pergunta 2", "Segunda."
    ]


@respx.mock
def test_history_sent_to_model_is_capped_at_20(client, use, engine):
    me_ok()
    conv_id = make_conversation(engine, n_messages=30)  # m0 (user) … m29 (assistant)
    calls = use([final(fontes=[])])

    assert post(client, "nova", conv_id).status_code == 200

    sent = calls.calls[0]["messages"]
    assert len(sent) == 21
    assert sent[0] == {"role": "user", "content": "m10"}
    assert sent[-2] == {"role": "assistant", "content": "m29"}
    assert sent[-1] == {"role": "user", "content": "nova"}


@respx.mock
def test_old_front_body_seeds_the_new_conversation(client, use, engine):
    me_ok()
    calls = use([final(resposta="R2", fontes=[])])
    seed = [
        {"role": "assistant", "content": "Olá! Como posso ajudar?"},  # saudação: descartada
        {"role": "user", "content": "Primeira pergunta do front antigo"},
        {"role": "assistant", "content": "R1"},
        {"role": "user", "content": "pergunta que deu 503"},
    ]

    r = post(client, "Pergunta nova", history_=seed)

    assert r.status_code == 200
    assert calls.calls[0]["messages"] == [
        {"role": "user", "content": "Primeira pergunta do front antigo"},
        {"role": "assistant", "content": "R1"},
        {"role": "user", "content": "pergunta que deu 503\n\nPergunta nova"},  # mesmo papel em sequência: unidos
    ]
    [conv] = all_rows(engine, AssistantConversation)
    assert conv.titulo == "Primeira pergunta do front antigo"
    rows = all_rows(engine, AssistantMessage)
    assert [(m.role, m.content) for m in rows] == [
        ("user", "Primeira pergunta do front antigo"),
        ("assistant", "R1"),
        ("user", "pergunta que deu 503"),
        ("user", "Pergunta nova"),
        ("assistant", "R2"),
    ]
    assert rows[1].fontes == [] and rows[1].result is None and rows[1].model is None


@respx.mock
def test_only_assistant_seed_is_dropped(client, use, engine):
    me_ok()
    calls = use([final(fontes=[])])
    r = post(client, "Oi", history_=[{"role": "assistant", "content": "Olá!"}])
    assert r.status_code == 200
    assert calls.calls[0]["messages"] == [{"role": "user", "content": "Oi"}]
    assert [m.content for m in all_rows(engine, AssistantMessage)][0] == "Oi"
    assert all_rows(engine, AssistantConversation)[0].titulo == "Oi"


# --- dono e 404 ------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["other_sub", "deleted", "unknown", "garbage"])
@respx.mock
def test_post_with_foreign_deleted_or_unknown_conversation_is_404(client, use, engine, kind):
    me_ok()
    conv_id = {
        "other_sub": lambda: make_conversation(engine, sub="auth0|outro", n_messages=2),
        "deleted": lambda: make_conversation(engine, n_messages=2, deleted_at=datetime.now(timezone.utc)),
        "unknown": lambda: str(uuid.uuid4()),
        "garbage": lambda: "nao-e-uuid",
    }[kind]()
    calls = use([final()])
    before_msgs = len(all_rows(engine, AssistantMessage))
    before = counter(ASSISTANT_MESSAGES, result="not_found")

    r = post(client, "Oi", conv_id)

    assert r.status_code == 404
    assert r.json() == {"error": "CONVERSA_NAO_ENCONTRADA"}
    assert calls.calls == []
    assert len(all_rows(engine, AssistantMessage)) == before_msgs
    assert counter(ASSISTANT_MESSAGES, result="not_found") == before + 1


@respx.mock
def test_429_and_409_create_nothing(client, use, engine, limiter):
    use([final(fontes=[])])
    respx.get(f"{app_onb()}/me").mock(
        return_value=httpx.Response(200, json={"customer_id": 7, "onboarding_status": "EM_ANALISE"})
    )
    for _ in range(10):
        limiter.acquire(SUB)
    assert post(client, "Oi").status_code == 429
    limiter.clear()
    assert post(client, "Oi").status_code == 409
    assert all_rows(engine, AssistantConversation) == []
    assert all_rows(engine, AssistantMessage) == []


def app_onb():
    from app.config import settings

    return settings.onboarding_api_url


def test_503_without_key_creates_nothing(client, engine, limiter):
    from app.routers.assistant import get_assistant_service, get_rate_limiter

    app.dependency_overrides[get_assistant_service] = lambda: None
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    assert post(client, "Oi").status_code == 503
    assert all_rows(engine, AssistantConversation) == []


# --- erros e recusa --------------------------------------------------------------------------


@respx.mock
def test_llm_error_keeps_user_message_and_audit_row(client, use, engine):
    me_ok()
    respx.get(f"{TX}/me/overview").mock(return_value=httpx.Response(200, json=OVERVIEW))
    calls = use([
        usage(message(tool_use("t", "consultar_visao_geral"), stop_reason="tool_use"), 900, 40),
        usage(message(text("não é JSON")), 1100, 60),
        usage(final(resposta="Agora sim.", fontes=[]), 300, 20),
    ])

    r = post(client, "Pergunta que falha")

    assert r.status_code == 503
    conv_id = r.json()["conversation_id"]
    assert r.json() == {"error": "ASSISTENTE_INDISPONIVEL", "conversation_id": conv_id}
    user, audit = all_rows(engine, AssistantMessage)
    assert (user.role, user.content) == ("user", "Pergunta que falha")
    assert (audit.role, audit.content, audit.result) == ("assistant", "", "error")
    assert (audit.input_tokens, audit.output_tokens) == (2000, 100)
    assert audit.model == "claude-sonnet-5-5" and audit.latency_ms >= 0

    # a linha de auditoria não aparece na API nem no histórico; a pergunta repetida é unida à anterior
    detail = client.get(f"{CONVERSATIONS}/{conv_id}").json()
    assert [m["content"] for m in detail["mensagens"]] == ["Pergunta que falha"]
    assert client.get(CONVERSATIONS).json()[0]["mensagens"] == 1
    assert post(client, "Pergunta que falha", conv_id).status_code == 200
    assert calls.calls[2]["messages"] == [{"role": "user", "content": "Pergunta que falha\n\nPergunta que falha"}]


@respx.mock
def test_refusal_is_persisted_as_refused(client, use, engine):
    me_ok()
    use([message(text("{"), stop_reason="refusal")])
    r = post(client, "Me ajude com outra coisa")
    assert r.status_code == 200 and r.json()["resposta"] == REFUSAL_TEXT
    assistant = all_rows(engine, AssistantMessage)[-1]
    assert assistant.result == "refused" and assistant.content == REFUSAL_TEXT


# --- tokens, latência e o que nunca é salvo ----------------------------------------------------


@respx.mock
def test_tokens_model_and_latency_are_saved(client, use, engine):
    me_ok()
    respx.get(f"{TX}/me/overview").mock(return_value=httpx.Response(200, json=OVERVIEW))
    in_before = counter(ASSISTANT_TOKENS, direction="input")
    out_before = counter(ASSISTANT_TOKENS, direction="output")
    use(
        [
            usage(message(tool_use("t", "consultar_visao_geral"), stop_reason="tool_use"), 1500, 80),
            usage(final(fontes=[]), 2100, 250, model="claude-opus-5-5"),
        ],
        on_call=lambda n: time.sleep(0.03),
    )

    r = post(client, "Como está meu caixa?")

    assert r.status_code == 200
    row = all_rows(engine, AssistantMessage)[-1]
    assert (row.input_tokens, row.output_tokens) == (3600, 330)
    assert row.model == "claude-opus-5-5"  # o modelo que de fato respondeu
    assert row.latency_ms >= 60
    assert row.fontes == [{"tipo": "overview", "label": "Visão geral"}]
    assert counter(ASSISTANT_TOKENS, direction="input") == in_before + 3600
    assert counter(ASSISTANT_TOKENS, direction="output") == out_before + 330


@respx.mock
def test_token_headers_and_tool_outputs_are_never_persisted(client, use, engine):
    me_ok()
    respx.get(f"{TX}/me/overview").mock(return_value=httpx.Response(200, json=OVERVIEW))
    use([
        message(tool_use("t", "consultar_visao_geral"), stop_reason="tool_use"),
        final(resposta="Seu saldo está positivo.", fontes=[]),
    ])
    assert post(client, "Qual meu saldo?").status_code == 200

    dump = ""
    for model in (AssistantConversation, AssistantMessage):
        for row in all_rows(engine, model):
            dump += json.dumps({c.name: getattr(row, c.name) for c in model.__table__.columns}, default=str)
    assert "test-token" not in dump and "Bearer" not in dump and "Authorization" not in dump
    assert "182000" not in dump and "receita_30d" not in dump and "18250" not in dump  # saída da ferramenta
    assert "tool_use" not in dump and "tool_result" not in dump


@respx.mock
def test_no_db_connection_is_held_during_llm_call(client, use, tmp_path, business_payload):
    """Pool real (QueuePool num SQLite em arquivo): nenhuma conexão emprestada enquanto o Claude responde."""
    from app.analysis.repository import BusinessAnalysisRepository
    from app.analysis.service import run_analysis

    engine = create_engine(f"sqlite:///{tmp_path / 'pool.db'}", pool_size=1, max_overflow=0)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as s:
        BusinessAnalysisRepository(s).save_outcome(7, run_analysis(business_payload), "e1")
        s.commit()
    app.dependency_overrides[get_session_factory] = lambda: factory
    me_ok()
    checked_out = []
    calls = use(
        [message(tool_use("a", "consultar_analise"), stop_reason="tool_use"), final(fontes=[])],
        on_call=lambda n: checked_out.append(engine.pool.checkedout()),
    )
    try:
        r = post(client, "Como está minha análise?")
        assert r.status_code == 200
        assert checked_out == [0, 0]  # nem na 1ª chamada nem depois da ferramenta consultar_analise
        assert '"faixa_risco"' in calls.calls[1]["messages"][-1]["content"][0]["content"]
        assert engine.pool.checkedout() == 0
        with factory() as s:
            assert s.scalar(select(AssistantMessage.result).where(AssistantMessage.role == "assistant")) == "ok"
    finally:
        engine.dispose()


# --- listar, ler e apagar ----------------------------------------------------------------------


@respx.mock
def test_list_conversations_newest_first_with_pagination(client, engine):
    me_ok()
    base = datetime(2026, 11, 1, tzinfo=timezone.utc)
    ids = [make_conversation(engine, n_messages=i + 1, updated_at=base + timedelta(hours=i), titulo=f"C{i}")
           for i in range(3)]
    make_conversation(engine, sub="auth0|outro", n_messages=2)
    make_conversation(engine, n_messages=2, deleted_at=base)

    r = client.get(CONVERSATIONS)

    assert r.status_code == 200
    assert r.json() == [
        {"id": ids[2], "titulo": "C2", "updated_at": "2026-11-01T02:00:00Z", "mensagens": 3},
        {"id": ids[1], "titulo": "C1", "updated_at": "2026-11-01T01:00:00Z", "mensagens": 2},
        {"id": ids[0], "titulo": "C0", "updated_at": "2026-11-01T00:00:00Z", "mensagens": 1},
    ]
    assert [c["id"] for c in client.get(CONVERSATIONS, params={"limit": 1, "offset": 1}).json()] == [ids[1]]
    assert client.get(CONVERSATIONS, params={"offset": 5}).json() == []
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        assert client.get(CONVERSATIONS, params=params).status_code == 422
    assert client.get(CONVERSATIONS, params={"limit": 100}).status_code == 200


@respx.mock
def test_get_conversation_messages(client, use, engine):
    me_ok()
    respx.get(f"{TX}/me/overview").mock(return_value=httpx.Response(200, json=OVERVIEW))
    use([message(tool_use("t", "consultar_visao_geral"), stop_reason="tool_use"), final()])
    created = post(client, "Qual é o meu saldo?").json()

    r = client.get(f"{CONVERSATIONS}/{created['conversation_id']}")

    assert r.status_code == 200
    body = r.json()
    assert body["id"] == created["conversation_id"] and body["titulo"] == "Qual é o meu saldo?"
    user, assistant = body["mensagens"]
    assert user["role"] == "user" and user["content"] == "Qual é o meu saldo?"
    assert user["fontes"] == [] and user["acoes"] == [] and user["sugestoes"] == []
    assert set(assistant) == {"id", "role", "content", "fontes", "acoes", "sugestoes", "created_at"}
    assert assistant["id"] == created["message_id"]
    assert assistant["content"] == created["resposta"]
    assert assistant["fontes"] == created["fontes"] == [{"tipo": "overview", "label": "Visão geral"}]
    assert assistant["acoes"] == created["acoes"] and assistant["sugestoes"] == created["sugestoes"]
    assert assistant["created_at"].endswith("Z")


@respx.mock
def test_get_conversation_404s(client, engine):
    me_ok()
    other = make_conversation(engine, sub="auth0|outro", n_messages=2)
    deleted = make_conversation(engine, deleted_at=datetime.now(timezone.utc))
    for conv_id in (other, deleted, str(uuid.uuid4()), "xyz"):
        r = client.get(f"{CONVERSATIONS}/{conv_id}")
        assert r.status_code == 404 and r.json() == {"error": "CONVERSA_NAO_ENCONTRADA"}


@respx.mock
def test_delete_one_conversation(client, engine):
    me_ok()
    mine = make_conversation(engine, n_messages=2)
    keep = make_conversation(engine, n_messages=2)
    other = make_conversation(engine, sub="auth0|outro", n_messages=2)

    r = client.delete(f"{CONVERSATIONS}/{mine}")

    assert r.status_code == 204 and r.content == b""
    assert [c["id"] for c in client.get(CONVERSATIONS).json()] == [keep]
    assert client.get(f"{CONVERSATIONS}/{mine}").status_code == 404
    assert client.delete(f"{CONVERSATIONS}/{mine}").json() == {"error": "CONVERSA_NAO_ENCONTRADA"}
    assert client.delete(f"{CONVERSATIONS}/{other}").status_code == 404  # não é dele
    rows = {str(c.id): c for c in all_rows(engine, AssistantConversation)}
    assert rows[mine].deleted_at is not None  # soft delete: mensagens continuam até a purga
    assert rows[other].deleted_at is None
    assert len(all_rows(engine, AssistantMessage)) == 6


@respx.mock
def test_delete_all_conversations_only_for_this_sub(client, engine):
    me_ok()
    make_conversation(engine, n_messages=2)
    make_conversation(engine, n_messages=2)
    other = make_conversation(engine, sub="auth0|outro", n_messages=2)

    assert client.delete(CONVERSATIONS).status_code == 204
    assert client.get(CONVERSATIONS).json() == []
    assert client.delete(CONVERSATIONS).status_code == 204  # idempotente

    as_user("auth0|outro")
    assert [c["id"] for c in client.get(CONVERSATIONS).json()] == [other]


def test_history_endpoints_require_read_self(client, engine):
    conv_id = make_conversation(engine)
    as_user(SUB, frozenset({"read:scores"}))
    assert client.get(CONVERSATIONS).status_code == 403
    assert client.get(f"{CONVERSATIONS}/{conv_id}").status_code == 403
    assert client.delete(f"{CONVERSATIONS}/{conv_id}").status_code == 403
    assert client.delete(CONVERSATIONS).status_code == 403


# --- retenção --------------------------------------------------------------------------------


def test_purge_hard_deletes_conversations_deleted_over_30_days_ago(engine):
    now = datetime(2026, 12, 31, 12, tzinfo=timezone.utc)
    old = make_conversation(engine, n_messages=3, deleted_at=now - timedelta(days=30, seconds=1))
    recent = make_conversation(engine, n_messages=2, deleted_at=now - timedelta(days=29))
    active = make_conversation(engine, n_messages=2)
    factory = sessionmaker(bind=engine)

    assert history.purge_deleted(factory, 30, now=lambda: now) == 1

    assert {str(c.id) for c in all_rows(engine, AssistantConversation)} == {recent, active}
    assert all(str(m.conversation_id) != old for m in all_rows(engine, AssistantMessage))
    assert len(all_rows(engine, AssistantMessage)) == 4
    assert history.purge_deleted(factory, 30, now=lambda: now) == 0
    assert history.purge_deleted(factory, 30, now=lambda: now + timedelta(days=2)) == 1  # a recente venceu


async def test_purge_loop_runs_at_startup_and_every_interval(engine, monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr(history, "purge_deleted", lambda factory, days: calls.append(days) or 0)

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await history.purge_loop(sessionmaker(bind=engine), 30, 6, sleep=fake_sleep)
    assert calls == [30, 30] and sleeps == [6 * 3600, 6 * 3600]


async def test_purge_loop_survives_errors(caplog):
    def broken():
        raise RuntimeError("banco fora")

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await history.purge_loop(broken, 30, 0, sleep=fake_sleep)
    assert sleeps == [36.0]  # intervalo mínimo
    assert "RuntimeError" in caplog.text and "banco fora" not in caplog.text


# --- unidades --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, expected",
    [
        ("Oi", "Oi"),
        ("  várias   palavras\n com espaços ", "várias palavras com espaços"),
        ("a" * 80, "a" * 80),
        ("a" * 81, "a" * 79 + "…"),
    ],
)
def test_make_titulo(question, expected):
    assert history.make_titulo(question) == expected


def test_make_titulo_cuts_at_word_boundary():
    question = "Quanto consigo antecipar dos meus recebíveis sem apertar o caixa de dezembro, considerando as contas?"
    titulo = history.make_titulo(question)
    assert len(titulo) <= 80 and titulo.endswith("…")
    assert titulo == "Quanto consigo antecipar dos meus recebíveis sem apertar o caixa de dezembro…"
    assert question.startswith(titulo[:-1])


def test_iso_handles_naive_and_aware():
    assert history.iso(datetime(2026, 11, 3, 12, 0)) == "2026-11-03T12:00:00Z"
    assert history.iso(datetime(2026, 11, 3, 9, 0, tzinfo=timezone(timedelta(hours=-3)))) == "2026-11-03T12:00:00Z"


def test_metrics_series_exist(client):
    body = client.get("/metrics").text
    assert "assistant_conversations_total " in body
    assert 'assistant_tokens_total{direction="input"}' in body
    assert 'assistant_tokens_total{direction="output"}' in body
    assert 'assistant_messages_total{result="not_found"}' in body
