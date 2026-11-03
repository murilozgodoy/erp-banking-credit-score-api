"""Ferramentas do Assistente ErpIa: somente leitura e simulação.

Cada ferramenta tem um JSON Schema (enviado ao Claude com `strict: true`) e um modelo pydantic equivalente que
revalida a entrada aqui antes de qualquer chamada HTTP. Um erro de ferramenta (entrada inválida, HTTP 4xx/5xx,
timeout) volta ao modelo como `tool_result` com `is_error`; não derruba a resposta.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.analysis.llm import _as_data
from app.clients.transactions_client import TransactionsClient, TransactionsClientError
from app.metrics import ASSISTANT_TOOL_CALLS, ASSISTANT_TOOLS

logger = logging.getLogger(__name__)

PRODUTOS = (
    "ANTECIPACAO_RECEBIVEIS",
    "CAPITAL_DE_GIRO",
    "ALAVANCAGEM",
    "FINANCIAMENTO_ESTOQUE",
    "CONSOLIDACAO_DIVIDAS",
)
GROUP_BY = ("day", "week", "month")
STATUS_RECEBIVEL = ("PREVISTO", "RECEBIDO", "ANTECIPADO")
STATUS_CONTA = ("ABERTO", "PAGO", "ATRASADO", "AGENDADO")
FONTE_TIPOS = ("analise", "overview", "vendas", "recebiveis", "pagamentos", "estoque", "pendencias", "credito")

# ferramenta -> (tipo da fonte no contrato, rótulo padrão)
TOOL_FONTE: dict[str, tuple[str, str]] = {
    "consultar_visao_geral": ("overview", "Visão geral"),
    "consultar_vendas": ("vendas", "Vendas"),
    "consultar_recebiveis": ("recebiveis", "Recebíveis"),
    "consultar_contas_a_pagar": ("pagamentos", "Contas a pagar"),
    "consultar_estoque": ("estoque", "Estoque"),
    "consultar_pendencias": ("pendencias", "Pendências"),
    "consultar_ofertas_credito": ("credito", "Ofertas de crédito"),
    "simular_credito": ("credito", "Simulação de crédito"),
    "consultar_analise": ("analise", "Análise do negócio"),
}

# limites para manter o tamanho das saídas (e os tokens) sob controle
DEFAULT_LIST_CAP = 20
LIST_CAPS = {"fluxo_caixa": 60, "serie": 62, "parcelas": 36, "itens": 25, "top_produtos": 10, "por_semana": 14}
MAX_STRING = 300
MAX_TOOL_CHARS = 20_000
MAX_DEPTH = 6

_DATE = {"type": "string", "format": "date", "description": "Data ISO (AAAA-MM-DD)."}


def _schema(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "consultar_visao_geral",
        "description": (
            "Visão geral da empresa: saldo da conta, receita dos últimos 30 dias e variação, ticket médio, "
            "recebíveis dos próximos 90 dias, contas a pagar em 30 dias, atrasados, valor em estoque, número de "
            "pendências e o fluxo de caixa projetado dia a dia para 60 dias (entradas, saídas, saldo projetado)."
        ),
        "input_schema": _schema(),
    },
    {
        "name": "consultar_vendas",
        "description": (
            "Vendas no período: série temporal (valor e quantidade), divisão por forma de pagamento e por canal, "
            "produtos mais vendidos, total e ticket médio. Sem datas, usa o período padrão do serviço."
        ),
        "input_schema": _schema(
            {
                "from": {**_DATE, "description": "Início do período (AAAA-MM-DD)."},
                "to": {**_DATE, "description": "Fim do período (AAAA-MM-DD)."},
                "group_by": {"type": "string", "enum": list(GROUP_BY), "description": "Agrupamento da série."},
            }
        ),
    },
    {
        "name": "consultar_recebiveis",
        "description": (
            "Agenda de recebíveis (vendas no cartão a receber): itens com id, vencimento, valor bruto e líquido, "
            "adquirente e status, total previsto e total por semana."
        ),
        "input_schema": _schema(
            {
                "status": {"type": "string", "enum": list(STATUS_RECEBIVEL)},
                "from": {**_DATE, "description": "Vencimento a partir de (AAAA-MM-DD)."},
                "to": {**_DATE, "description": "Vencimento até (AAAA-MM-DD)."},
            }
        ),
    },
    {
        "name": "consultar_contas_a_pagar",
        "description": (
            "Contas a pagar (fornecedores, folha, impostos, aluguel, empréstimos): itens, total em aberto, "
            "total atrasado e o que vence nos próximos 7 dias."
        ),
        "input_schema": _schema({"status": {"type": "string", "enum": list(STATUS_CONTA)}}),
    },
    {
        "name": "consultar_estoque",
        "description": (
            "Estoque: itens (quantidade, mínimo, custo, preço, giro em dias, última venda), valor total, valor "
            "parado (giro acima de 90 dias), itens abaixo do mínimo e cobertura média em dias."
        ),
        "input_schema": _schema(),
    },
    {
        "name": "consultar_pendencias",
        "description": (
            "Pendências da empresa: contas atrasadas, contas que vencem hoje, estoque baixo ou parado, saldo "
            "insuficiente e propostas de crédito, com valor, data e a ação sugerida."
        ),
        "input_schema": _schema(),
    },
    {
        "name": "consultar_ofertas_credito",
        "description": "Limites de crédito pré-aprovados por produto, com taxa mensal a partir de e prazo máximo.",
        "input_schema": _schema(),
    },
    {
        "name": "simular_credito",
        "description": (
            "Simula um crédito da ErpIa (só cálculo, não contrata nada): devolve valor líquido, taxa mensal, CET "
            "anual, IOF, parcelas, total pago, aprovação (PRE_APROVADO, ANALISE ou NEGADO) e limite disponível. "
            "Para antecipação, informe os ids dos recebíveis escolhidos quando o cliente indicar quais."
        ),
        "input_schema": _schema(
            {
                "produto": {"type": "string", "enum": list(PRODUTOS)},
                "valor": {"type": "number", "description": "Valor solicitado em reais (maior que zero)."},
                "prazo_meses": {"type": "integer", "description": "Prazo em meses (1 a 36)."},
                "receivable_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Ids dos recebíveis a antecipar (opcional).",
                },
            },
            ["produto", "valor", "prazo_meses"],
        ),
    },
    {
        "name": "consultar_analise",
        "description": (
            "Análise do negócio feita pela ErpIa no onboarding: score (0 a 1000), faixa de risco, resumo, KPIs com "
            "benchmark do segmento, insights por área, oportunidades de crédito com valores, pontos fortes e riscos."
        ),
        "input_schema": _schema(),
    },
]
for _tool in TOOL_DEFINITIONS:
    _tool["strict"] = True
TOOL_NAMES = tuple(tool["name"] for tool in TOOL_DEFINITIONS)
assert TOOL_NAMES == ASSISTANT_TOOLS, "ferramentas e séries de métrica fora de sincronia"


# --- validação das entradas (espelha os JSON Schemas acima) ---------------------------------


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _iso_date(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("use AAAA-MM-DD")
    date.fromisoformat(value)  # ValueError em data inexistente (ex.: 2026-02-30)
    return value


class _Periodo(_Input):
    date_from: str | None = Field(default=None, alias="from")
    date_to: str | None = Field(default=None, alias="to")

    @field_validator("date_from", "date_to", mode="before")
    @classmethod
    def _dates(cls, value):
        return _iso_date(value)

    @model_validator(mode="after")
    def _ordem(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("'from' depois de 'to'")
        return self


class NoInput(_Input):
    pass


class VendasInput(_Periodo):
    group_by: Literal["day", "week", "month"] | None = None


class RecebiveisInput(_Periodo):
    status: Literal["PREVISTO", "RECEBIDO", "ANTECIPADO"] | None = None


class ContasInput(_Input):
    status: Literal["ABERTO", "PAGO", "ATRASADO", "AGENDADO"] | None = None


class SimulacaoInput(_Input):
    produto: Literal[
        "ANTECIPACAO_RECEBIVEIS", "CAPITAL_DE_GIRO", "ALAVANCAGEM", "FINANCIAMENTO_ESTOQUE", "CONSOLIDACAO_DIVIDAS"
    ]
    valor: float = Field(gt=0, le=1e12, allow_inf_nan=False)
    prazo_meses: int = Field(ge=1, le=36)
    receivable_ids: list[int] | None = Field(default=None, max_length=500)


def _query(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(by_alias=True, exclude_none=True)


@dataclass(frozen=True)
class ToolOutcome:
    content: str
    is_error: bool = False
    fonte: tuple[str, str] | None = None  # (tipo, rótulo padrão) quando a ferramenta trouxe dados


class ToolExecutor:
    """Executa as ferramentas para um cliente: Transactions com o token dele e a análise do próprio Score."""

    def __init__(self, transactions: TransactionsClient, analysis_provider: Callable[[], dict | None]):
        self.transactions = transactions
        self.analysis_provider = analysis_provider

    def run(self, name: str, raw_input: Any, timeout: float) -> ToolOutcome:
        if name not in TOOL_FONTE:
            return ToolOutcome("Ferramenta desconhecida.", is_error=True)
        ASSISTANT_TOOL_CALLS.labels(tool=name).inc()
        try:
            data = self._dispatch(name, raw_input if isinstance(raw_input, dict) else {}, timeout)
        except ValidationError as exc:
            campos = sorted({".".join(str(p) for p in err["loc"]) or "entrada" for err in exc.errors()})
            return ToolOutcome(f"Parâmetros inválidos: {', '.join(campos)[:200]}.", is_error=True)
        except TransactionsClientError as exc:
            logger.warning("ferramenta do assistente falhou tool=%s status=%s", name, exc.status_code)
            return ToolOutcome(f"Não foi possível obter os dados: {exc}.", is_error=True)
        except Exception as exc:  # noqa: BLE001 — erro inesperado vira tool_result de erro
            logger.warning("ferramenta do assistente falhou tool=%s erro=%s", name, type(exc).__name__)
            return ToolOutcome("Erro interno ao obter os dados.", is_error=True)
        return ToolOutcome(render(data), fonte=TOOL_FONTE[name])

    def _dispatch(self, name: str, args: dict, timeout: float) -> Any:
        tx = self.transactions
        if name == "consultar_visao_geral":
            NoInput.model_validate(args)
            return tx.get("/me/overview", timeout=timeout)
        if name == "consultar_vendas":
            return tx.get("/me/sales", params=_query(VendasInput.model_validate(args)), timeout=timeout)
        if name == "consultar_recebiveis":
            return tx.get("/me/receivables", params=_query(RecebiveisInput.model_validate(args)), timeout=timeout)
        if name == "consultar_contas_a_pagar":
            return tx.get("/me/payables", params=_query(ContasInput.model_validate(args)), timeout=timeout)
        if name == "consultar_estoque":
            NoInput.model_validate(args)
            return tx.get("/me/inventory", timeout=timeout)
        if name == "consultar_pendencias":
            NoInput.model_validate(args)
            return tx.get("/me/pendencias", timeout=timeout)
        if name == "consultar_ofertas_credito":
            NoInput.model_validate(args)
            return tx.get("/me/credit/offers", timeout=timeout)
        if name == "simular_credito":
            body = _query(SimulacaoInput.model_validate(args))
            return tx.post("/me/credit/simulations", body, timeout=timeout)
        # consultar_analise: a análise vigente vem do próprio Score, sem HTTP
        NoInput.model_validate(args)
        analysis = self.analysis_provider()
        return analysis if analysis is not None else {"status": "SEM_ANALISE", "detalhe": "nenhuma análise encontrada"}


# --- corte das saídas ------------------------------------------------------------------------


def trim(value: Any, key: str | None = None, depth: int = 0) -> Any:
    """Limita listas (por chave), strings e profundidade; registra quantos itens foram omitidos."""
    if depth > MAX_DEPTH:
        return "…"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out[k] = trim(v, k, depth + 1)
            if isinstance(v, list):
                omitted = len(v) - len(out[k])
                if omitted > 0:
                    out[f"{k}_omitidos"] = omitted
        return out
    if isinstance(value, list):
        cap = LIST_CAPS.get(key or "", DEFAULT_LIST_CAP)
        return [trim(item, None, depth + 1) for item in value[:cap]]
    if isinstance(value, str) and len(value) > MAX_STRING:
        return value[:MAX_STRING] + "…"
    return value


def render(data: Any) -> str:
    """JSON enxuto e neutralizado (`<`/`>` escapados) para devolver ao modelo como dado."""
    trimmed = trim(data)
    if isinstance(data, list) and len(data) > len(trimmed):
        trimmed = {"itens": trimmed, "itens_omitidos": len(data) - len(trimmed)}
    text = _as_data(trimmed)
    if len(text) > MAX_TOOL_CHARS:
        text = text[:MAX_TOOL_CHARS] + "… (truncado)"
    return text

