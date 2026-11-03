"""Fase 2: permissões de membro (`membro` em GET /me) no Assistente e MEMBRO_SUSPENSO."""
import httpx
import pytest
import respx

from app.assistant.agent import SEM_FINANCEIRO_TEXT
from app.clients.onboarding_client import (
    MeCache,
    MemberInfo,
    MemberSuspendedError,
    OnboardingClient,
    OnboardingForbiddenError,
)
from app.config import Settings
from app.metrics import ASSISTANT_MESSAGES, ASSISTANT_RESULTS
from tests.test_assistant_api import ONB, ask, counter, final, use, limiter  # noqa: F401  (fixtures)

SEM_PERMISSAO = {"error": "SEM_PERMISSAO", "permissao": "usar_assistente"}
SUSPENSO = {"error": "MEMBRO_SUSPENSO"}
HISTORY_ROUTES = [
    ("get", "/me/assistant/conversations"),
    ("get", "/me/assistant/conversations/00000000-0000-0000-0000-000000000000"),
    ("delete", "/me/assistant/conversations/00000000-0000-0000-0000-000000000000"),
    ("delete", "/me/assistant/conversations"),
]


def membro(*permissoes, status="ATIVO"):
    return {"id": 3, "papel": "OPERADOR", "permissoes": list(permissoes), "limite_pagamento": 500, "status": status}


def me_with(member):
    body = {"customer_id": 7, "onboarding_status": "APROVADO"}
    if member != "absent":
        body["membro"] = member
    return respx.get(f"{ONB}/me").mock(return_value=httpx.Response(200, json=body))


# --- cliente /me -------------------------------------------------------------------------------


@respx.mock
def test_get_me_parses_membro():
    me_with(membro("usar_assistente", "ver_financeiro"))
    me = OnboardingClient(base_url=ONB).get_me()
    assert me.membro == MemberInfo(3, "OPERADOR", frozenset({"usar_assistente", "ver_financeiro"}), 500.0, "ATIVO")
    assert me.membro.can("ver_financeiro") and not me.membro.can("pagar")


@pytest.mark.parametrize("member", ["absent", None, "lixo"])
@respx.mock
def test_get_me_without_membro_is_backward_compatible(member):
    me_with(member)
    assert OnboardingClient(base_url=ONB).get_me().membro is None


@respx.mock
def test_get_me_membro_without_limit():
    me_with({"id": 1, "papel": "DONO", "permissoes": None})
    m = OnboardingClient(base_url=ONB).get_me().membro
    assert m.limite_pagamento is None and m.permissoes == frozenset()


@respx.mock
def test_get_me_suspended_vs_generic_403():
    route = respx.get(f"{ONB}/me")
    route.mock(return_value=httpx.Response(403, json=SUSPENSO))
    with pytest.raises(MemberSuspendedError):
        OnboardingClient(base_url=ONB).get_me()
    route.mock(return_value=httpx.Response(403, text="nao-json"))
    with pytest.raises(OnboardingForbiddenError) as exc:
        OnboardingClient(base_url=ONB).get_me()
    assert not isinstance(exc.value, MemberSuspendedError)
    route.mock(return_value=httpx.Response(403, json=["x"]))
    with pytest.raises(OnboardingForbiddenError) as exc:
        OnboardingClient(base_url=ONB).get_me()
    assert not isinstance(exc.value, MemberSuspendedError)


def test_me_cache_ttl_defaults_to_60s_and_is_configurable(monkeypatch):
    assert Settings().me_cache_ttl_seconds == 60.0
    monkeypatch.setenv("ME_CACHE_TTL_SECONDS", "15")
    assert Settings().me_cache_ttl_seconds == 15.0
    assert MeCache().ttl == 60.0
    assert MeCache(ttl_seconds=5).ttl == 5


# --- POST /me/assistant/messages ---------------------------------------------------------------


def test_sem_permissao_is_zero_initialised():
    assert "sem_permissao" in ASSISTANT_RESULTS


@respx.mock
def test_member_without_usar_assistente_gets_403_before_rate_limit(client, use, limiter):
    me_with(membro("ver_financeiro"))
    calls = use([])
    for _ in range(10):
        limiter.acquire("auth0|test")  # mesmo esgotado, a permissão vem antes do 429
    before = counter(ASSISTANT_MESSAGES, result="sem_permissao")

    r = ask(client)

    assert r.status_code == 403 and r.json() == SEM_PERMISSAO
    assert counter(ASSISTANT_MESSAGES, result="sem_permissao") == before + 1
    assert calls.calls == []


@respx.mock
def test_member_with_both_permissions_gets_tools(client, use):
    me_with(membro("usar_assistente", "ver_financeiro"))
    calls = use([final(fontes=[])])
    assert ask(client).status_code == 200
    kwargs = calls.calls[0]
    assert kwargs["tools"]
    assert all(b["text"] != SEM_FINANCEIRO_TEXT for b in kwargs["system"])


@pytest.mark.parametrize("member", ["absent", None])
@respx.mock
def test_no_membro_keeps_old_behaviour(client, use, member):
    me_with(member)
    calls = use([final(fontes=[])])
    assert ask(client).status_code == 200
    assert calls.calls[0]["tools"]


@respx.mock
def test_member_without_ver_financeiro_gets_no_tools(client, use):
    me_with(membro("usar_assistente"))
    calls = use([final(fontes=[], resposta="Peça acesso a um administrador.")])
    r = ask(client)
    assert r.status_code == 200
    kwargs = calls.calls[0]
    assert "tools" not in kwargs and "tool_choice" not in kwargs
    assert kwargs["system"][-1]["text"] == SEM_FINANCEIRO_TEXT
    assert "administrador" in SEM_FINANCEIRO_TEXT


@respx.mock
def test_suspended_member_on_post(client, use):
    respx.get(f"{ONB}/me").mock(return_value=httpx.Response(403, json=SUSPENSO))
    use([])
    r = ask(client)
    assert r.status_code == 403 and r.json() == SUSPENSO


@respx.mock
def test_suspended_member_on_analysis(client):
    respx.get(f"{ONB}/me").mock(return_value=httpx.Response(403, json=SUSPENSO))
    r = client.get("/me/analysis")
    assert r.status_code == 403 and r.json() == SUSPENSO


# --- histórico ---------------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", HISTORY_ROUTES)
@respx.mock
def test_history_routes_require_usar_assistente(client, method, path):
    me_with(membro("ver_financeiro"))
    r = client.request(method, path)
    assert r.status_code == 403 and r.json() == SEM_PERMISSAO


@pytest.mark.parametrize("method,path", HISTORY_ROUTES)
@respx.mock
def test_history_routes_suspended(client, method, path):
    respx.get(f"{ONB}/me").mock(return_value=httpx.Response(403, json=SUSPENSO))
    r = client.request(method, path)
    assert r.status_code == 403 and r.json() == SUSPENSO


@pytest.mark.parametrize("member", ["absent", None, membro("usar_assistente")])
@respx.mock
def test_history_routes_allowed(client, member):
    me_with(member)
    assert client.get("/me/assistant/conversations").status_code == 200
