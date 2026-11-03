# ErpIa - Credit Score API

Serviço do **ErpIa** para cálculo e histórico de score de crédito e para a **análise do negócio** do onboarding PJ. Usa padrões **Strategy** (algoritmos de score) e **Factory** (seleção de strategy).
Documentação central do projeto em [erp-banking-front/docs](https://github.com/murilozgodoy/erp-banking-front/tree/main/docs).

## Requisitos

- Python 3.12+ (o CI e a imagem Docker usam 3.13)
- Onboarding API rodando (para chamadas reais). Nos testes é mockado com `respx`.

## Instalação

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env
```

## Executar

```bash
python -m app.main          # http://localhost:8082 (porta configurável por PORT)
```

Docs interativas: http://localhost:8082/docs (exigem Bearer token).

## Produção (EC2 + API Gateway)

O serviço roda no `docker compose` do repo [erp-banking-infra](https://github.com/GabrielRosa1/erp-banking-infra)
na EC2 `3.235.6.136`, **sem porta publicada**: o acesso público é só pelo API Gateway (nginx) na porta **8080**, com
o prefixo `/score` (removido antes de chegar aqui; por isso `ROOT_PATH=/score`).

| O quê            | URL                                                        |
| ---------------- | ---------------------------------------------------------- |
| API              | `http://3.235.6.136:8080/score/...` (ex.: `/score/health`) |
| Swagger          | `http://3.235.6.136:8080/score/docs` (exige Bearer token)  |
| Front (Vercel)   | https://erp-banking-front.vercel.app → `/api/score/*` reescrito para o gateway |
| Grafana          | `http://3.235.6.136:8081`                                  |
| Prometheus       | `http://3.235.6.136:8082` (coleta `credit-score-api:8082/metrics` pela rede interna) |

`/metrics` não é exposto pelo gateway. No compose: `ONBOARDING_API_URL=http://onboarding-api:8081`,
`RABBITMQ_URL=amqp://erp:erp@rabbitmq:5672/`, `DATABASE_URL` com `search_path%3Dcredit_score`.

## Testes com cobertura

```bash
pytest
```

Cobertura mínima: **80%**.

## Configuração (`.env`)

| Variável             | Padrão                        | Descrição                                                   |
| -------------------- | ----------------------------- | ----------------------------------------------------------- |
| `DATABASE_URL`       | `sqlite:///./credit_score.db` | Em produção: `postgresql+psycopg://erp:erp@postgres:5432/erp?options=-csearch_path%3Dcredit_score` (schema criado no startup) |
| `ONBOARDING_API_URL` | `http://localhost:8081`       | URL da Onboarding API (compose: `http://onboarding-api:8081`) |
| `PORT`               | `8082`                        | Porta do uvicorn (local e dentro do container)              |
| `AUTH0_DOMAIN`       | —                             | Domínio do tenant, sem `https://`                           |
| `AUTH0_AUDIENCE`     | —                             | Identifier da API no Auth0: `https://erp-banking-api`       |
| `CORS_ORIGINS`       | `["http://localhost:5173"]`   | Origens do frontend, em JSON                                |
| `ROOT_PATH`          | vazio                         | Prefixo no API Gateway (`/score`), para o `/docs` funcionar |
| `RABBITMQ_URL`       | vazio                         | `amqp://erp:erp@rabbitmq:5672/`; vazio = consumer desligado |
| `LOG_LEVEL`          | `INFO`                        | Nível dos logs JSON                                         |

## Endpoints

| Método | Rota                                  | Permissão      | Descrição                                                          |
| ------ | ------------------------------------- | -------------- | ------------------------------------------------------------------ |
| GET    | `/health`                             | pública        | Health check                                                       |
| POST   | `/scores/evaluations`                 | `write:scores` | Roda uma avaliação (consulta o Onboarding, aplica a strategy, persiste) |
| GET    | `/scores/evaluations?customer_id=`    | `read:scores`  | Histórico de avaliações do cliente (mais recente primeiro)         |
| GET    | `/scores/customers/{id}/latest`       | `read:scores`  | Avaliação vigente do cliente (usada pela Transactions API)         |
| GET    | `/scores/strategies`                  | `read:scores`  | Strategies registradas na `ScoreStrategyFactory`                   |
| GET    | `/scores/decisions?customer_id=`      | `read:scores`  | Decisões de crédito recebidas via evento `credit.decided` (mais recente primeiro) |
| GET    | `/scores/customers/{id}/status`       | `read:scores`  | `desatualizado: true` se a anamnese mudou depois da última avaliação |
| GET    | `/scores/analyses/{customer_id}`      | `read:scores`  | Versão mais recente da análise do negócio do cliente (404 se não houver) |
| GET    | `/me/analysis`                        | `read:self`    | Análise do negócio do cliente dono do token (ver abaixo)           |
| GET    | `/metrics`                            | pública        | Métricas Prometheus (não exposta pelo gateway)                     |

### Strategies disponíveis

- `weighted` (padrão): renda 40% + patrimônio 30% + comprometimento 30%
- `simple`: regras binárias de renda/dívidas/comprometimento

## Análise do negócio (onboarding PJ)

Contrato: `PORTAL_CLIENTE.md`, seção 4. Pacote `app/analysis/`:

- **Strategy `BusinessAnalyzer`** → `RuleBasedAnalyzer` (sempre roda, determinístico). A partir das respostas do
  template calcula os KPIs (`receita_mensal`, `margem_bruta_pct`, `ticket_medio`, `prazo_medio_recebimento_dias`,
  `prazo_medio_pagamento_dias`, `ciclo_caixa_dias`, `endividamento_sobre_receita`, `comprometimento_parcelas_pct` e,
  com estoque, `cobertura_estoque_dias`), cada um com `benchmark_segmento` da tabela em `benchmarks.py` e uma
  `leitura`. Resposta ausente → `valor: null`, `leitura: "não informado"`, e a regra não pontua.
- **Score do negócio (0–1000):** base 500 + pontos por receita vs. segmento, margem, endividamento, parcelas, ciclo de
  caixa, folha, sazonalidade, estoque parado e churn (SaaS). Faixa com os mesmos cortes das strategies:
  BAIXO ≥ 750, MEDIO ≥ 500, ALTO ≥ 300, MUITO_ALTO abaixo disso.
- **Oportunidades** (seção 5): taxa base BAIXO 1,69% · MEDIO 2,49% · ALTO 3,49% · MUITO_ALTO sem oferta; ANTECIPACAO
  −0,4 pp (até 80% dos recebíveis de cartão estimados), FINANCIAMENTO_ESTOQUE −0,2 pp (até 60% do estoque),
  CAPITAL_DE_GIRO 0 (receita diária × ciclo de caixa, até 1× a receita), ALAVANCAGEM +0,3 pp (só BAIXO/MEDIO com
  dívida ≤ 1,5× e parcelas ≤ 20%; parcela livre de 25% da receita em 36 meses, até 3× a receita) e
  CONSOLIDACAO_DIVIDAS (dívidas com parcelas > 15% ou dívida > 1× a receita).
- **Insights** por área (`geral|vendas|recebiveis|pagamentos|estoque|pendencias|credito`, severidade
  `info|atencao|oportunidade`), pontos fortes, riscos e resumo.
- **Orquestrador** `app/analysis/service.py`: `run_analysis(payload, llm=None) -> AnalysisOutcome`. O `llm` é qualquer
  objeto com `analyze(payload, rules_result) -> dict | None`. Com veredito: `gerado_por = "llm+regras"`, o resumo do
  LLM substitui o das regras, pontos fortes e riscos são unidos sem duplicata e os insights do LLM são anexados
  (área/severidade inválidas viram `geral`/`info`). Sem LLM, com `None` ou exceção: `gerado_por = "regras"`.

**Status:** `APROVADO`, ou **`ANALISE_MANUAL`** se qualquer um destes ocorrer:

| Motivo | Regra |
| --- | --- |
| `SEGMENTO_OUTRO` | `segmento == "outro"` |
| `LLM_ESPECIFICO` | LLM respondeu `enquadramento == "ESPECIFICO"` |
| `LLM_BAIXA_CONFIANCA` | LLM respondeu `confianca < 0.6` |
| `RECEITA_INVALIDA` | `faturamento_mensal` ausente, zero ou negativo |
| `DIVIDA_EXCESSIVA` | `dividas_total > 3 × faturamento_mensal` |
| `PARCELAS_EXCESSIVAS` | `parcela_dividas_mensal > 50%` do faturamento |
| `MARGEM_NEGATIVA` | `margem_bruta_pct < 0` |
| `RISCO_MUITO_ALTO` | score do negócio na faixa MUITO_ALTO |

As regras críticas também aparecem em `riscos`. A decisão humana registra `APROVADO` ou `REPROVADO` numa nova versão.

**Persistência:** `business_analyses` (versionada por cliente: `customer_id`, `version`, `status`, `score`,
`faixa_risco`, `gerado_por`, `resultado` JSON com o corpo completo, `source_event_id` único, `reviewer`,
`review_comment`, `created_at`); as rotas devolvem sempre a maior versão, com `atualizado_em = created_at`.

**`GET /me/analysis`:** o `customer_id` vem do `GET /me` do Onboarding com o token repassado (cache em memória de 5 min
por `sub`, só quando já existe customer). Sem customer → 404 `{"error":"ONBOARDING_NAO_INICIADO","status":...}`;
onboarding `EM_ANALISE` sem análise ainda → 200 `{customer_id, status:"EM_ANALISE"}` com os demais campos nulos/vazios;
outro status sem análise → 404 `{"error":"ANALISE_NAO_ENCONTRADA","status":...}`. Falta de permissão no Onboarding →
403; Onboarding fora → 502.

**`motivos_revisao`:** `GET /me/analysis` e `GET /scores/analyses/{id}` trazem `motivos_revisao: [{codigo, descricao}]` (ex.: `SEGMENTO_OUTRO`, `LLM_ESPECIFICO`, `DIVIDA_EXCESSIVA`, descrição em pt-BR); lista vazia em `APROVADO` e em análises gravadas antes do campo.

**Métricas:** `business_analyses_total{status,gerado_por,segmento}` (uma por análise concluída, séries zeradas para os
9 segmentos) e `llm_requests_total{result="disabled"}` quando a análise roda sem LLM configurado.

## Análise do negócio por LLM (ErpIa)

`app/analysis/llm.py` (`LlmAnalyzer`) complementa a análise por regras do onboarding PJ. Recebe o payload de
`customer.onboarding_submitted` (segmento, empresa, respostas, observações) e, se houver, o resultado das regras, e
pede ao Claude um JSON estruturado (structured outputs com JSON Schema):
`{enquadramento: PADRAO|ESPECIFICO, confianca: 0..1, resumo, pontos_fortes[], riscos[], insights[]}`. O retorno é
validado com pydantic (confiança limitada a 0..1, insights inválidos descartados, listas com tamanho máximo).

| Variável | Padrão | Uso |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | vazio | Vazio = LLM desligado, só regras |
| `ANALYSIS_MODEL` | `claude-sonnet-5-5` | Modelo usado |
| `ANALYSIS_TIMEOUT_SECONDS` | `20` | Timeout da chamada (1 retry) |

**Fallback:** qualquer falha (timeout, erro da API, recusa, saída inválida) devolve `None` e a análise segue só com
as regras. Métricas: `llm_requests_total{result="ok|error|timeout|disabled"}` (saída inválida ou recusa
contam como `error`; `disabled` é contado pelo orquestrador quando não há chave) e o histograma
`llm_request_duration_seconds`, observado em toda chamada real à API.

**Privacidade e segurança:** a chave nunca vai para log, exceção, `repr` ou resposta (o log registra só o tipo da
falha, o resultado e a latência, sem conteúdo). As respostas e observações do cliente são enviadas à API da
Anthropic como dados delimitados, e o prompt manda ignorar instruções contidas nelas. Os testes usam um client falso
e nunca chamam a API.

## Autenticação e autorização (Auth0)

Envie o **access token** do Auth0 em `Authorization: Bearer <token>`. A API valida assinatura
RS256 via JWKS, issuer, audience e expiração: **401** sem token ou com token inválido, **403**
quando falta a permissão da rota (claim `permissions` do RBAC do Auth0, ou `scope`) e **503** se
`AUTH0_*` não estiver configurado. `/health` e `/metrics` são públicos; `/docs`, `/redoc` e `/openapi.json` exigem token.

**Token relay:** ao calcular um score, o `OnboardingClient` repassa o mesmo token ao Onboarding,
que aplica as próprias permissões. Por isso calcular score exige `write:scores` aqui **e**
`read:customers` no Onboarding; se faltar a segunda, a resposta é 403 com o motivo.

Os testes usam chaves RSA e tokens locais ou autenticação simulada, sem chamadas ao Auth0.

## Etapa 4 — eventos, métricas, logs e Docker

**Eventos (RabbitMQ, aio-pika).** Com `RABBITMQ_URL` definido, um consumer roda em background no lifespan
(reconexão automática a cada 5 s) e declara a exchange `erp.events` (topic, durável):

| Fila (durável)                     | Routing key                  | Efeito |
| ---------------------------------- | ---------------------------- | ------ |
| `credit-score.credit-decided`      | `credit.decided`             | Grava em `credit_decision_events` (idempotente por `event_id`) → `GET /scores/decisions` |
| `credit-score.anamnesis-updated`   | `customer.anamnesis_updated` | Marca o score do cliente como desatualizado (`customer_score_status`); nova avaliação limpa a marca |
| `credit-score.onboarding-submitted` | `customer.onboarding_submitted` | Roda a análise do negócio (regras + LLM se `ANTHROPIC_API_KEY`), grava nova versão em `business_analyses` e publica `business.analysis_completed` |
| `credit-score.onboarding-reviewed` | `customer.onboarding_reviewed` | Grava a decisão humana (`APROVADO`/`REPROVADO`, `reviewer`, `comentario`) como nova versão da análise vigente |

Todos idempotentes por `event_id` (`source_event_id` único em `business_analyses`). As filas são declaradas duráveis e
sem argumentos (as definitions do broker já existem assim).

**Publicação:** `business.analysis_completed` `{event_id, event_type, occurred_at, customer_id, status, score,
faixa_risco, gerado_por}` na exchange `erp.events`. Os handlers são síncronos (thread, sessão própria) e devolvem os
eventos a publicar (`HandlerOutcome`); a camada assíncrona publica **depois do commit e antes do ack**, com o
`EventPublisher` (aio-pika, conexão preguiçosa protegida por `asyncio.Lock`, mensagem persistente). Falha de
publicação é logada e contada em `events_published_total{routing_key,result="error"}`, nunca derruba o consumo.

JSON inválido é descartado (ack, `result=invalid`); erro inesperado volta para a fila uma vez e depois é descartado.

**Métricas** (`GET /metrics`): padrão do `prometheus-fastapi-instrumentator` (`http_requests_total`,
`http_request_duration_seconds`, `http_request_duration_highr_seconds`) +
`score_evaluations_total{strategy,faixa_risco}`, `events_consumed_total{routing_key,result=ok|duplicate|invalid|error}` e
`events_published_total{routing_key,result=ok|error}` (séries zeradas no startup).

**Logs:** uma linha JSON por requisição (`request_id`, `method`, `path`, `status`, `duration_ms`); o `X-Request-ID`
recebido do gateway é reaproveitado (ou gerado) e devolvido no header da resposta.

**Docker:** `docker build -t credit-score-api .` → imagem `python:3.13-slim`, usuário não-root, `HEALTHCHECK` em
`/health`, porta via `PORT` (8082), 1 worker. Em push na `main` o CI publica
`ghcr.io/murilozgodoy/erp-banking-credit-score-api:latest` e `:<sha>`.
