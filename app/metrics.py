"""Métricas de negócio expostas em /metrics (nomes fixados no contrato da Etapa 4)."""
from prometheus_client import Counter

SCORE_EVALUATIONS = Counter(
    "score_evaluations_total",
    "Avaliações de score realizadas",
    ["strategy", "faixa_risco"],
)

EVENTS_CONSUMED = Counter(
    "events_consumed_total",
    "Eventos consumidos do RabbitMQ",
    ["routing_key", "result"],
)

LLM_ANALYSES = Counter(
    "llm_analyses_total",
    "Análises de negócio feitas pelo LLM",
    ["result"],
)

CONSUMED_ROUTING_KEYS = ("credit.decided", "customer.anamnesis_updated")
CONSUMED_RESULTS = ("ok", "duplicate", "invalid", "error")
STRATEGIES = ("simple", "weighted")
FAIXAS_RISCO = ("BAIXO", "MEDIO", "ALTO", "MUITO_ALTO")
LLM_RESULTS = ("ok", "error", "timeout", "invalid")


def init_metrics() -> None:
    """Cria as séries rotuladas com valor 0 para o Grafana não exibir "No data".

    Idempotente: `.labels(...)` sem `.inc()` só registra a série.
    """
    for routing_key in CONSUMED_ROUTING_KEYS:
        for result in CONSUMED_RESULTS:
            EVENTS_CONSUMED.labels(routing_key=routing_key, result=result)
    for strategy in STRATEGIES:
        for faixa in FAIXAS_RISCO:
            SCORE_EVALUATIONS.labels(strategy=strategy, faixa_risco=faixa)
    for result in LLM_RESULTS:
        LLM_ANALYSES.labels(result=result)


init_metrics()
