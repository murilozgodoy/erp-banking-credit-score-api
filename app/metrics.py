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
