"""Métricas de negócio expostas em /metrics (nomes fixados no contrato da Etapa 4)."""
from prometheus_client import Counter, Histogram

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

LLM_REQUESTS = Counter(
    "llm_requests_total",
    "Análises de negócio pelo LLM (disabled = sem ANTHROPIC_API_KEY)",
    ["result"],
)

LLM_REQUEST_DURATION = Histogram(
    "llm_request_duration_seconds",
    "Duração das chamadas reais à API do LLM (sucesso, timeout e erro)",
    buckets=(0.5, 1, 2, 5, 10, 15, 20, 30, 45, 60),
)

BUSINESS_ANALYSES = Counter(
    "business_analyses_total",
    "Análises do negócio concluídas (não conta a revisão humana)",
    ["status", "gerado_por", "segmento"],
)

CONSUMED_ROUTING_KEYS = ("credit.decided", "customer.anamnesis_updated")
CONSUMED_RESULTS = ("ok", "duplicate", "invalid", "error")
STRATEGIES = ("simple", "weighted")
FAIXAS_RISCO = ("BAIXO", "MEDIO", "ALTO", "MUITO_ALTO")
LLM_RESULTS = ("ok", "error", "timeout", "disabled")
ANALYSIS_STATUSES = ("APROVADO", "ANALISE_MANUAL")
ANALYSIS_GERADO_POR = ("regras", "llm+regras")
SEGMENTOS = (
    "varejo", "alimentacao", "servicos", "saude", "tecnologia",
    "industria", "agro", "construcao", "outro",
)


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
        LLM_REQUESTS.labels(result=result)
    for status in ANALYSIS_STATUSES:
        for gerado_por in ANALYSIS_GERADO_POR:
            for segmento in SEGMENTOS:
                BUSINESS_ANALYSES.labels(status=status, gerado_por=gerado_por, segmento=segmento)


init_metrics()
