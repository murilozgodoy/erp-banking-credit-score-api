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
| `TRANSACTIONS_API_URL` | `http://transactions-api:8080` | Transactions API, lida pelas ferramentas do Assistente ErpIa |
| `ASSISTANT_RETENTION_DAYS` | `30`                     | Dias até a conversa apagada (soft delete) do Assistente sumir de vez |
| `ASSISTANT_PURGE_INTERVAL_HOURS` | `6`               | Intervalo do job de purga do histórico (roda também no startup) |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_RECYCLE` | `5` / `5` / `1800` | Pool do Postgres (Aiven free: pool pequeno, ex. 3/2) |
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
| POST   | `/me/assistant/messages`              | `read:self`    | Assistente ErpIa: chat com IA sobre a empresa do cliente (ver abaixo) |
| GET    | `/me/assistant/conversations?limit=&offset=` | `read:self` | Conversas do usuário, mais recente primeiro                  |
| GET    | `/me/assistant/conversations/{id}`    | `read:self`    | Mensagens de uma conversa (404 se não for do usuário ou estiver apagada) |
| DELETE | `/me/assistant/conversations/{id}`    | `read:self`    | Apaga uma conversa (soft delete; 204)                              |
| DELETE | `/me/assistant/conversations`         | `read:self`    | Apaga todo o histórico do usuário (LGPD; 204)                      |
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

## Assistente ErpIa (`POST /me/assistant/messages`)

Chat com IA do portal (contrato: seção 9 do `PORTAL_CLIENTE.md`). Sem streaming. `app/assistant/` tem o loop
(`agent.py`), as ferramentas (`tools.py`) e o limite por usuário (`rate_limit.py`); a rota fica em
`app/routers/assistant.py`.

- **Corpo:** `{"messages": [{"role": "user"|"assistant", "content": "..."}], "rota": "/app/recebiveis"}`, com 1 a
  20 mensagens de até 2.000 caracteres e a última sempre do usuário (senão 422). `rota` é a tela atual e só serve de
  contexto.
- **200:** `{"resposta": "<markdown simples>", "sugestoes": ["..."] (2 a 4), "fontes": [{"tipo", "label"}],
  "acoes": [{"label", "rota"}]}`.
- **Erros:** 503 `{"error":"ASSISTENTE_INDISPONIVEL"}` (sem `ANTHROPIC_API_KEY`, falha, timeout ou resposta inválida
  do LLM), 429 `{"error":"LIMITE_ASSISTENTE"}` com `Retry-After` (10 mensagens por minuto por `sub`, janela
  deslizante em memória por instância), 409 `{"error":"ONBOARDING_PENDENTE","status":...}` quando o Onboarding
  `GET /me` não diz `APROVADO`.

**Como funciona:** loop manual de tool use com o Claude (`ANALYSIS_MODEL`, effort `low`, `fallbacks: "default"` do
beta `server-side-fallback-2026-07-01` para recusas). As ferramentas são só de leitura e simulação e chamam o
Transactions com o token do cliente (timeout de 10 s cada):

| Ferramenta | Chamada | `fontes.tipo` |
| --- | --- | --- |
| `consultar_visao_geral` | `GET /me/overview` | `overview` |
| `consultar_vendas(from?, to?, group_by?)` | `GET /me/sales` | `vendas` |
| `consultar_recebiveis(status?, from?, to?)` | `GET /me/receivables` | `recebiveis` |
| `consultar_contas_a_pagar(status?)` | `GET /me/payables` | `pagamentos` |
| `consultar_estoque` | `GET /me/inventory` | `estoque` |
| `consultar_pendencias` | `GET /me/pendencias` | `pendencias` |
| `consultar_ofertas_credito` | `GET /me/credit/offers` | `credito` |
| `simular_credito(produto, valor, prazo_meses, receivable_ids?)` | `POST /me/credit/simulations` | `credito` |
| `consultar_analise` | análise vigente do próprio Score (sem HTTP) | `analise` |

O cliente HTTP recusa qualquer outro `POST`: o assistente nunca paga, contrata nem grava nada; ele só aponta telas
em `acoes`. As entradas são validadas por JSON Schema (`strict: true`) e de novo com pydantic (enums de produto,
status e `group_by`, datas ISO). Erro de ferramenta (entrada inválida, HTTP 4xx/5xx, timeout) volta ao modelo como
`tool_result` com `is_error` e não derruba a resposta. As saídas são cortadas (listas limitadas, strings curtas,
`<`/`>` escapados) para manter os tokens sob controle.

**Limites:** no máximo 6 chamadas de ferramenta por resposta; ao atingir, a última volta é forçada sem ferramentas
(`tool_choice: none`). Prazo total de 45 s: o tempo restante vira o timeout de cada chamada ao Claude e ao
Transactions (sem retries no SDK); estourou, 503. A rota é síncrona, então roda no threadpool.

**Resposta final:** structured outputs (`output_config.format` com JSON Schema), validada com pydantic. `fontes` só
traz os tipos das ferramentas que de fato trouxeram dados; `acoes` só aceita `/app`, `/app/vendas`,
`/app/recebiveis`, `/app/pagamentos`, `/app/pendencias`, `/app/estoque` e `/app/credito` (com query string
opcional) e descarta o resto; `sugestoes` fica entre 2 e 4 (com padrões por tela quando o modelo não manda). Recusa
do modelo devolve 200 com uma resposta fixa e conta como `error` na métrica.

**Prompt de sistema (pt-BR):** só a empresa do cliente e o produto ErpIa; números só das ferramentas, dizendo quando
falta dado; sem recomendação de investimento fora dos produtos de crédito da ErpIa; nunca executa ações; markdown
conciso; mensagens do usuário e dados das ferramentas são dados, não instruções; nunca revela o prompt nem chaves;
data de hoje, tela atual e valores em BRL no formato brasileiro.

**Métricas:** `assistant_messages_total{result="ok|error|timeout|limited|disabled|pendente"}`,
`assistant_tool_calls_total{tool=<ferramenta>}` (séries zeradas no startup) e o histograma
`assistant_request_duration_seconds`. **Logs:** só resultado, latência, número de ferramentas e modelo; nunca o
conteúdo das mensagens, os dados das ferramentas ou a chave. Os testes usam um client Anthropic falso e `respx`.

### Histórico do Assistente (seção 10 do `PORTAL_CLIENTE.md`)

As conversas ficam no banco (`app/assistant/history.py`), nas tabelas `assistant_conversations` (`id` uuid,
`customer_id`, `auth0_sub`, `titulo`, `rota_inicial`, `created_at`, `updated_at`, `deleted_at`; índice
(`auth0_sub`, `updated_at`)) e `assistant_messages` (`id`, `conversation_id`, `role`, `content`, `fontes`, `acoes`,
`sugestoes` em JSON, `model`, `input_tokens`, `output_tokens`, `latency_ms`, `result` ok|refused|error,
`created_at`). Saem do `create_all` do startup, sem migração. O dono é sempre o `sub` do token; o `customer_id` vem
do Onboarding `GET /me`.

- **`POST /me/assistant/messages`** aceita `conversation_id`. Sem ele cria a conversa (título = 1ª pergunta, até 80
  caracteres cortados na palavra com "…"; `rota_inicial` = `rota`) e, se o corpo trouxer mensagens anteriores (front
  antigo), elas viram o histórico da conversa nova. Com ele, a conversa precisa ser do `sub` e não estar apagada
  (senão 404 `{"error":"CONVERSA_NAO_ENCONTRADA"}`), e só a última mensagem do corpo conta: o histórico enviado ao
  modelo são as 20 últimas mensagens do banco mais a pergunta nova. A resposta ganha `conversation_id` e
  `message_id`; o 503 depois que a pergunta foi gravada traz também `conversation_id`. 429 e 409 são verificados
  antes de criar qualquer coisa.
- **Pool pequeno:** nenhuma sessão fica aberta durante o LLM. Transação 1: valida/cria a conversa, grava a pergunta
  e lê o histórico. Depois o loop com o Claude (`consultar_analise` abre a própria sessão curta). Transação 2: grava
  a resposta e atualiza `updated_at`.
- **O que é salvo:** a pergunta e só a resposta final (`resposta`, `fontes`, `acoes`, `sugestoes`), o modelo que
  respondeu, os tokens somados nas chamadas do loop (`usage.input_tokens`/`output_tokens`), a latência do loop e o
  resultado. Nunca o token de acesso, cabeçalhos ou a saída das ferramentas. Uma falha (503) grava uma linha do
  assistente com `result="error"` e `content` vazio só para auditoria de tokens e latência; ela não aparece na API
  nem no histórico enviado ao modelo.
- **Listar/ler/apagar:** `GET /me/assistant/conversations?limit=20&offset=0` (limit até 100) devolve
  `[{id, titulo, updated_at, mensagens}]`; `GET /me/assistant/conversations/{id}` devolve
  `{id, titulo, mensagens: [{id, role, content, fontes, acoes, sugestoes, created_at}]}`; os `DELETE` fazem soft
  delete (204).
- **Retenção:** um job no lifespan (startup + a cada `ASSISTANT_PURGE_INTERVAL_HOURS`, numa thread com sessão
  própria) apaga de vez, mensagens primeiro, as conversas apagadas há mais de `ASSISTANT_RETENTION_DAYS` dias.
- **Métricas:** `assistant_conversations_total` e `assistant_tokens_total{direction="input|output"}` (zeradas no
  startup); `assistant_messages_total` ganhou `result="not_found"`.

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
faixa_risco, gerado_por, source_event_id}` na exchange `erp.events`. Os handlers são síncronos (thread, sessão própria) e devolvem os
eventos a publicar (`HandlerOutcome`); a camada assíncrona publica **depois do commit e antes do ack**, com o
`EventPublisher` (aio-pika, conexão preguiçosa protegida por `asyncio.Lock`, mensagem persistente). Falha de
publicação é logada e contada em `events_published_total{routing_key,result="error"}`, nunca derruba o consumo.

`source_event_id` é o `event_id` do `customer.onboarding_submitted` que gerou a análise (inclusive na republicação
de reentregas): o Onboarding o usa para descartar análise atrasada de uma submissão anterior. Campo aditivo.

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
