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

EVENTS_PUBLISHED = Counter(
    "events_published_total",
    "Eventos publicados no RabbitMQ",
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

ASSISTANT_MESSAGES = Counter(
    "assistant_messages_total",
    "Mensagens ao Assistente ErpIa por resultado (disabled = sem ANTHROPIC_API_KEY, limited = 429, pendente = 409, "
    "refused = recusa do modelo)",
    ["result"],
)

ASSISTANT_TOOL_CALLS = Counter(
    "assistant_tool_calls_total",
    "Chamadas de ferramenta feitas pelo Assistente ErpIa (inclui as que falharam)",
    ["tool"],
)

ASSISTANT_REQUEST_DURATION = Histogram(
    "assistant_request_duration_seconds",
    "Duração das respostas do Assistente ErpIa que chegaram a chamar o LLM",
    buckets=(1, 2, 5, 10, 15, 20, 30, 45, 60),
)

CONSUMED_ROUTING_KEYS = (
    "credit.decided",
    "customer.anamnesis_updated",
    "customer.onboarding_submitted",
    "customer.onboarding_reviewed",
)
PUBLISHED_ROUTING_KEYS = ("business.analysis_completed",)
PUBLISHED_RESULTS = ("ok", "error")
CONSUMED_RESULTS = ("ok", "duplicate", "invalid", "error")
STRATEGIES = ("simple", "weighted")
FAIXAS_RISCO = ("BAIXO", "MEDIO", "ALTO", "MUITO_ALTO")
LLM_RESULTS = ("ok", "error", "timeout", "disabled")
ANALYSIS_STATUSES = ("APROVADO", "ANALISE_MANUAL")
ANALYSIS_GERADO_POR = ("regras", "llm+regras")
ASSISTANT_RESULTS = ("ok", "error", "timeout", "limited", "disabled", "pendente", "refused")
ASSISTANT_TOOLS = (
    "consultar_visao_geral",
    "consultar_vendas",
    "consultar_recebiveis",
    "consultar_contas_a_pagar",
    "consultar_estoque",
    "consultar_pendencias",
    "consultar_ofertas_credito",
    "simular_credito",
    "consultar_analise",
)
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
    for routing_key in PUBLISHED_ROUTING_KEYS:
        for result in PUBLISHED_RESULTS:
            EVENTS_PUBLISHED.labels(routing_key=routing_key, result=result)
    for strategy in STRATEGIES:
        for faixa in FAIXAS_RISCO:
            SCORE_EVALUATIONS.labels(strategy=strategy, faixa_risco=faixa)
    for result in LLM_RESULTS:
        LLM_REQUESTS.labels(result=result)
    for result in ASSISTANT_RESULTS:
        ASSISTANT_MESSAGES.labels(result=result)
    for tool in ASSISTANT_TOOLS:
        ASSISTANT_TOOL_CALLS.labels(tool=tool)
    for status in ANALYSIS_STATUSES:
        for gerado_por in ANALYSIS_GERADO_POR:
            for segmento in SEGMENTOS:
                BUSINESS_ANALYSES.labels(status=status, gerado_por=gerado_por, segmento=segmento)


init_metrics()
