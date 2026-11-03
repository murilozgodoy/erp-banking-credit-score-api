"""Revisão de segurança: isolamento do histórico do assistente, rotas de staff, /me/analysis e logs."""
import logging
import uuid
from datetime import datetime, timezone

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.repository import BusinessAnalysisRepository
from app.analysis.service import run_analysis
from app.auth import Principal, require_auth
from app.clients.onboarding_client import me_cache
from app.logging_config import JsonFormatter
from app.main import app
from app.messaging.handlers import handle_onboarding_submitted
from app.models import AssistantConversation, AssistantMessage
from tests.test_assistant_api import ONB, URL, final, limiter, use  # noqa: F401  (fixtures)

SUB = "auth0|test"
COLEGA = "auth0|colega"  # outro membro da MESMA empresa (customer 7)
TOKEN = "eyJhbGciOiJSUzI1NiJ9.token-secreto-score.assinatura"
CNPJ = "11222333000181"
CLIENTE = frozenset({"read:self", "write:self"})
CONVERSATIONS = "/me/assistant/conversations"


def as_user(sub=SUB, permissions=frozenset({"read:self"})):
    app.dependency_overrides[require_auth] = lambda: Principal(TOKEN, sub, permissions)


def me_returns(customer_id=7, status="APROVADO", membro=None):
    me_cache.clear()
    body = {"customer_id": customer_id, "onboarding_status": status,
            "empresa": {"razao_social": "Loja LTDA", "cnpj": CNPJ}}
    if membro is not None:
        body["membro"] = membro
    return respx.get(f"{ONB}/me").mock(return_value=httpx.Response(200, json=body))


def make_conversation(engine, sub, customer_id=7, n_messages=2):
    ts = datetime.now(timezone.utc)
    with Session(engine) as s:
        conv = AssistantConversation(id=uuid.uuid4(), customer_id=customer_id, auth0_sub=sub, titulo="Conversa",
                                     rota_inicial="/app", created_at=ts, updated_at=ts)
        s.add(conv)
        s.flush()
        for i in range(n_messages):
            role = "user" if i % 2 == 0 else "assistant"
            extra = {"fontes": [], "acoes": [], "sugestoes": [], "result": "ok"} if role == "assistant" else {}
            s.add(AssistantMessage(conversation_id=conv.id, role=role, content=f"dado-de-{sub}-{i}",
                                   created_at=ts, **extra))
        s.commit()
        return str(conv.id)


def state(engine, conversation_id):
    with Session(engine) as s:
        conv = s.get(AssistantConversation, uuid.UUID(conversation_id))
        n = s.scalar(select(func.count(AssistantMessage.id))
                     .where(AssistantMessage.conversation_id == conv.id))
        return conv.deleted_at, n, conv.updated_at


def ask(client, conversation_id=None, content="Quanto tenho a receber?"):
    body = {"messages": [{"role": "user", "content": content}]}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    return client.post(URL, json=body)


NOT_FOUND = {"error": "CONVERSA_NAO_ENCONTRADA"}


# ---------- conversas de outro usuário (inclusive da mesma empresa) ----------

@respx.mock
def test_conversation_of_another_user_is_404_and_untouched(client, engine, use):
    me_returns()
    calls = use([final()])
    alheia = make_conversation(engine, COLEGA)
    before = state(engine, alheia)
    as_user(SUB)
    for response in (
        client.get(f"{CONVERSATIONS}/{alheia}"),
        client.delete(f"{CONVERSATIONS}/{alheia}"),
        ask(client, alheia),
    ):
        assert response.status_code == 404 and response.json() == NOT_FOUND
    assert "dado-de-" not in client.get(CONVERSATIONS).text
    assert state(engine, alheia) == before  # nada apagado nem anexado
    assert calls.calls == []  # o LLM não foi chamado


@respx.mock
def test_delete_all_only_deletes_callers_conversations(client, engine):
    me_returns()
    minha = make_conversation(engine, SUB)
    alheia = make_conversation(engine, COLEGA)
    as_user(SUB)
    assert client.delete(CONVERSATIONS).status_code == 204
    assert state(engine, minha)[0] is not None
    assert state(engine, alheia)[0] is None
    as_user(COLEGA)
    assert [c["id"] for c in client.get(CONVERSATIONS).json()] == [alheia]


@respx.mock
def test_conversations_of_previous_company_are_hidden(client, engine, use):
    """Quem saiu da empresa 7 (e entrou na 8, ou ficou sem empresa) não relê nem continua conversas da 7."""
    calls = use([final()])
    antiga = make_conversation(engine, SUB, customer_id=7)
    before = state(engine, antiga)
    as_user(SUB)
    me_returns(customer_id=8)
    assert client.get(CONVERSATIONS).json() == []
    assert client.get(f"{CONVERSATIONS}/{antiga}").json() == NOT_FOUND
    assert ask(client, antiga).json() == NOT_FOUND
    assert state(engine, antiga) == before and calls.calls == []

    me_returns(customer_id=None, status="NAO_INICIADO")
    assert client.get(CONVERSATIONS).json() == []
    assert client.get(f"{CONVERSATIONS}/{antiga}").status_code == 404
    assert client.delete(f"{CONVERSATIONS}/{antiga}").status_code == 204  # apagar o próprio histórico (LGPD)

    me_returns(customer_id=7)
    assert client.get(f"{CONVERSATIONS}/{antiga}").status_code == 404  # já apagada


@pytest.mark.parametrize("bad_id", ["nao-e-uuid", "00000000-0000-0000-0000-000000000000"])
@respx.mock
def test_unknown_or_malformed_conversation_id_is_404(client, bad_id):
    me_returns()
    as_user(SUB)
    assert client.get(f"{CONVERSATIONS}/{bad_id}").json() == NOT_FOUND
    assert client.delete(f"{CONVERSATIONS}/{bad_id}").json() == NOT_FOUND


# ---------- permissões ----------

@respx.mock
def test_member_without_usar_assistente_is_403(client, engine, use):
    calls = use([final()])
    minha = make_conversation(engine, SUB)
    me_returns(membro={"id": 3, "papel": "LEITURA", "permissoes": ["ver_financeiro"], "status": "ATIVO"})
    as_user(SUB)
    denied = {"error": "SEM_PERMISSAO", "permissao": "usar_assistente"}
    for response in (
        ask(client), ask(client, minha), client.get(CONVERSATIONS), client.get(f"{CONVERSATIONS}/{minha}"),
        client.delete(f"{CONVERSATIONS}/{minha}"), client.delete(CONVERSATIONS),
    ):
        assert response.status_code == 403 and response.json() == denied
    assert state(engine, minha)[:2] == (None, 2) and calls.calls == []


@pytest.mark.parametrize("method, path", [
    ("get", "/me/analysis"),
    ("post", URL),
    ("get", CONVERSATIONS),
    ("get", f"{CONVERSATIONS}/00000000-0000-0000-0000-000000000000"),
    ("delete", f"{CONVERSATIONS}/00000000-0000-0000-0000-000000000000"),
    ("delete", CONVERSATIONS),
])
def test_token_without_read_self_is_403(client, method, path):
    as_user(SUB, frozenset({"write:self"}))
    kwargs = {"json": {"messages": [{"role": "user", "content": "oi"}]}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 403


@pytest.mark.parametrize("method, path", [
    ("get", "/scores/analyses/7"),
    ("get", "/scores/customers/7/latest"),
    ("get", "/scores/customers/7/status"),
    ("get", "/scores/evaluations"),
    ("get", "/scores/strategies"),
    ("get", "/scores/decisions"),
    ("post", "/scores/evaluations"),
])
def test_staff_routes_reject_client_token(client, method, path):
    as_user(SUB, CLIENTE)
    kwargs = {"json": {"customer_id": 7}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 403


# ---------- /me/analysis ----------

@respx.mock
def test_me_analysis_returns_only_callers_customer(client, db_session, business_payload):
    for customer_id in (7, 8):
        payload = {**business_payload, "customer_id": customer_id}
        BusinessAnalysisRepository(db_session).save_outcome(customer_id, run_analysis(payload), f"e{customer_id}")
    db_session.commit()
    as_user(SUB, CLIENTE)
    for customer_id in (7, 8):
        me_returns(customer_id=customer_id)
        assert client.get("/me/analysis").json()["customer_id"] == customer_id
    me_returns(customer_id=None, status="NAO_INICIADO")
    assert client.get("/me/analysis").json()["error"] == "ONBOARDING_NAO_INICIADO"


# ---------- logs ----------

def _all_logs(caplog) -> str:
    """Como o JsonFormatter grava; sem httpx (aqui é o cliente do TestClient e o respx)."""
    formatter = JsonFormatter()
    return "\n".join(formatter.format(r) for r in caplog.records if not r.name.startswith(("httpx", "httpcore")))


@respx.mock
def test_assistant_message_logs_no_token_question_or_cnpj(client, use, caplog):
    caplog.set_level(logging.DEBUG)
    me_returns()
    use([final()])
    as_user(SUB)
    question = "Pergunta-sensivel-sobre-faturamento"
    response = client.post(URL, json={"messages": [{"role": "user", "content": question}]},
                           headers={"Authorization": "Bearer header-secreto-123"})
    assert response.status_code == 200
    text = _all_logs(caplog)
    assert "assistente result=ok" in text and '"path": "/me/assistant/messages"' in text
    for secret in (TOKEN, "header-secreto-123", question, CNPJ, "R$ 120.000,00"):
        assert secret not in text


def test_invalid_submitted_event_log_has_no_payload(db_session, business_payload, caplog):
    caplog.set_level(logging.DEBUG)
    payload = {**business_payload, "customer_id": "nao-e-numero",
               "empresa": {"cnpj": CNPJ, "razao_social": "Loja LTDA"}}
    assert handle_onboarding_submitted(db_session, payload) == "invalid"
    text = _all_logs(caplog)
    assert "inválido" in text
    for secret in (CNPJ, "Loja LTDA", "nao-e-numero", business_payload["observacoes"], "faturamento_mensal"):
        assert secret not in text
