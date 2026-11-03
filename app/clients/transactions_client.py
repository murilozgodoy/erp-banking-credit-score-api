"""Cliente HTTP do Transactions (rotas `/me/*`) usado pelas ferramentas do Assistente ErpIa.

Repassa o access token do cliente (token relay): o Transactions resolve o customer pelo próprio token e aplica as
permissões dele. Só faz leituras (`GET`) e um único `POST` permitido, a simulação de crédito, que não grava contrato
nem movimenta dinheiro. Qualquer outro `POST` é recusado aqui, antes de sair da aplicação.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from app.config import settings

DEFAULT_TIMEOUT = 10.0
ALLOWED_POSTS = frozenset({"/me/credit/simulations"})
_ERROR_CODE = re.compile(r"^[A-Z][A-Z_]{1,59}$")


class TransactionsClientError(Exception):
    """Falha ao consultar o Transactions. A mensagem é curta e segura para devolver ao modelo."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class TransactionsClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        access_token: str | None = None,
    ):
        self.base_url = (base_url or settings.transactions_api_url).rstrip("/")
        self.timeout = timeout
        self.access_token = access_token

    def __repr__(self) -> str:  # o token nunca aparece em repr/log
        return f"TransactionsClient(base_url={self.base_url!r})"

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            return {}
        return {"Authorization": f"Bearer {self.access_token}"}

    def get(self, path: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        return self._request("GET", path, params=params, timeout=timeout)

    def post(self, path: str, body: dict[str, Any], timeout: float | None = None) -> Any:
        if path not in ALLOWED_POSTS:
            raise TransactionsClientError("operação de escrita não permitida")
        return self._request("POST", path, json=body, timeout=timeout)

    def _request(self, method: str, path: str, *, timeout: float | None, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        effective = min(self.timeout, timeout) if timeout is not None else self.timeout
        try:
            with httpx.Client(timeout=max(effective, 0.1)) as client:
                resp = client.request(method, url, headers=self._headers(), **kwargs)
        except httpx.TimeoutException as exc:
            raise TransactionsClientError("tempo esgotado ao consultar os dados") from exc
        except httpx.HTTPError as exc:
            raise TransactionsClientError("falha de conexão ao consultar os dados") from exc
        if resp.status_code >= 400:
            raise TransactionsClientError(_error_message(resp), status_code=resp.status_code)
        try:
            return resp.json()
        except ValueError as exc:
            raise TransactionsClientError("resposta inválida do serviço de dados") from exc


def _error_message(resp: httpx.Response) -> str:
    """`HTTP 409 (ONBOARDING_PENDENTE)`: só o status e, se houver, um código de erro em CAIXA_ALTA."""
    code = None
    try:
        body = resp.json()
        if isinstance(body, dict) and isinstance(body.get("error"), str) and _ERROR_CODE.match(body["error"]):
            code = body["error"]
    except ValueError:
        pass
    return f"HTTP {resp.status_code}" + (f" ({code})" if code else "")
