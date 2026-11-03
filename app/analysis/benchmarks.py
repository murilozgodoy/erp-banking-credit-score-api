"""Benchmarks por segmento (médias de referência, dados versionados no código).

Valores ilustrativos e estáveis: servem de régua para a leitura dos KPIs e para o score.
"""
from app.metrics import SEGMENTOS  # noqa: F401  (fonte única da lista de segmentos)

SEGMENTO_NOMES = {
    "varejo": "Varejo e e-commerce",
    "alimentacao": "Alimentação",
    "servicos": "Serviços",
    "saude": "Saúde",
    "tecnologia": "Tecnologia",
    "industria": "Indústria",
    "agro": "Agronegócio",
    "construcao": "Construção",
    "outro": "Outro segmento",
}

# receita_mensal (BRL), margem_bruta_pct (%), ticket_medio (BRL), prazos (dias),
# endividamento_sobre_receita (x a receita mensal), comprometimento_parcelas_pct (%),
# cobertura_estoque_dias (dias) e pct_cartao_credito (% das vendas, usado quando não informado).
BENCHMARKS: dict[str, dict[str, float]] = {
    "varejo": {
        "receita_mensal": 150_000, "margem_bruta_pct": 35, "ticket_medio": 120,
        "prazo_medio_recebimento_dias": 35, "prazo_medio_pagamento_dias": 40,
        "ciclo_caixa_dias": 55, "endividamento_sobre_receita": 1.0,
        "comprometimento_parcelas_pct": 12, "cobertura_estoque_dias": 60,
        "pct_cartao_credito": 55,
    },
    "alimentacao": {
        "receita_mensal": 90_000, "margem_bruta_pct": 60, "ticket_medio": 55,
        "prazo_medio_recebimento_dias": 20, "prazo_medio_pagamento_dias": 25,
        "ciclo_caixa_dias": 5, "endividamento_sobre_receita": 0.8,
        "comprometimento_parcelas_pct": 10, "cobertura_estoque_dias": 10,
        "pct_cartao_credito": 45,
    },
    "servicos": {
        "receita_mensal": 80_000, "margem_bruta_pct": 50, "ticket_medio": 1_500,
        "prazo_medio_recebimento_dias": 30, "prazo_medio_pagamento_dias": 20,
        "ciclo_caixa_dias": 10, "endividamento_sobre_receita": 0.7,
        "comprometimento_parcelas_pct": 10, "cobertura_estoque_dias": 15,
        "pct_cartao_credito": 25,
    },
    "saude": {
        "receita_mensal": 120_000, "margem_bruta_pct": 45, "ticket_medio": 350,
        "prazo_medio_recebimento_dias": 45, "prazo_medio_pagamento_dias": 30,
        "ciclo_caixa_dias": 20, "endividamento_sobre_receita": 1.0,
        "comprometimento_parcelas_pct": 12, "cobertura_estoque_dias": 30,
        "pct_cartao_credito": 40,
    },
    "tecnologia": {
        "receita_mensal": 100_000, "margem_bruta_pct": 70, "ticket_medio": 800,
        "prazo_medio_recebimento_dias": 30, "prazo_medio_pagamento_dias": 30,
        "ciclo_caixa_dias": 0, "endividamento_sobre_receita": 0.5,
        "comprometimento_parcelas_pct": 8, "cobertura_estoque_dias": 0,
        "pct_cartao_credito": 30,
    },
    "industria": {
        "receita_mensal": 400_000, "margem_bruta_pct": 30, "ticket_medio": 5_000,
        "prazo_medio_recebimento_dias": 50, "prazo_medio_pagamento_dias": 40,
        "ciclo_caixa_dias": 70, "endividamento_sobre_receita": 1.5,
        "comprometimento_parcelas_pct": 15, "cobertura_estoque_dias": 60,
        "pct_cartao_credito": 10,
    },
    "agro": {
        "receita_mensal": 300_000, "margem_bruta_pct": 25, "ticket_medio": 20_000,
        "prazo_medio_recebimento_dias": 60, "prazo_medio_pagamento_dias": 45,
        "ciclo_caixa_dias": 90, "endividamento_sobre_receita": 2.0,
        "comprometimento_parcelas_pct": 15, "cobertura_estoque_dias": 90,
        "pct_cartao_credito": 5,
    },
    "construcao": {
        "receita_mensal": 250_000, "margem_bruta_pct": 25, "ticket_medio": 15_000,
        "prazo_medio_recebimento_dias": 45, "prazo_medio_pagamento_dias": 35,
        "ciclo_caixa_dias": 40, "endividamento_sobre_receita": 1.5,
        "comprometimento_parcelas_pct": 15, "cobertura_estoque_dias": 30,
        "pct_cartao_credito": 10,
    },
    "outro": {
        "receita_mensal": 120_000, "margem_bruta_pct": 40, "ticket_medio": 500,
        "prazo_medio_recebimento_dias": 35, "prazo_medio_pagamento_dias": 30,
        "ciclo_caixa_dias": 30, "endividamento_sobre_receita": 1.0,
        "comprometimento_parcelas_pct": 12, "cobertura_estoque_dias": 45,
        "pct_cartao_credito": 30,
    },
}


def benchmark_for(segmento: str | None) -> dict[str, float]:
    """Benchmark do segmento; segmento desconhecido usa o de `outro`."""
    return BENCHMARKS.get((segmento or "").lower(), BENCHMARKS["outro"])
