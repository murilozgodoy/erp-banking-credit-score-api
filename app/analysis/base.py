"""Strategy da análise do negócio (contrato: PORTAL_CLIENTE.md, seção 4)."""
from abc import ABC, abstractmethod

AREAS = ("geral", "vendas", "recebiveis", "pagamentos", "estoque", "pendencias", "credito")
SEVERIDADES = ("info", "atencao", "oportunidade")
TIPOS_OPORTUNIDADE = (
    "ANTECIPACAO_RECEBIVEIS",
    "CAPITAL_DE_GIRO",
    "ALAVANCAGEM",
    "FINANCIAMENTO_ESTOQUE",
    "CONSOLIDACAO_DIVIDAS",
)
KPI_IDS = (
    "receita_mensal",
    "margem_bruta_pct",
    "ticket_medio",
    "prazo_medio_recebimento_dias",
    "prazo_medio_pagamento_dias",
    "ciclo_caixa_dias",
    "endividamento_sobre_receita",
    "comprometimento_parcelas_pct",
    "cobertura_estoque_dias",
)


class BusinessAnalyzer(ABC):
    """Interface do padrão Strategy para analisar o negócio a partir do onboarding.

    `analyze` recebe o payload de `customer.onboarding_submitted` e devolve o resultado das regras:
    `{score, faixa_risco, resumo, kpis[], insights[], oportunidades[], pontos_fortes[], riscos[],
    regras_criticas[]}`.
    """

    name: str = "base"

    @abstractmethod
    def analyze(self, payload: dict) -> dict:
        raise NotImplementedError
