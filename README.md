# ERP Banking - Credit Score API

Serviço de cálculo e histórico de score de crédito. Usa padrões **Strategy** (algoritmos de score) e **Factory** (seleção de strategy).
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
| GET    | `/metrics`                            | pública        | Métricas Prometheus (não exposta pelo gateway)                     |

### Strategies disponíveis

- `weighted` (padrão): renda 40% + patrimônio 30% + comprometimento 30%
- `simple`: regras binárias de renda/dívidas/comprometimento

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

JSON inválido é descartado (ack, `result=invalid`); erro inesperado volta para a fila uma vez e depois é descartado.

**Métricas** (`GET /metrics`): padrão do `prometheus-fastapi-instrumentator` (`http_requests_total`,
`http_request_duration_seconds`, `http_request_duration_highr_seconds`) +
`score_evaluations_total{strategy,faixa_risco}` e `events_consumed_total{routing_key,result=ok|duplicate|invalid|error}`.

**Logs:** uma linha JSON por requisição (`request_id`, `method`, `path`, `status`, `duration_ms`); o `X-Request-ID`
recebido do gateway é reaproveitado (ou gerado) e devolvido no header da resposta.

**Docker:** `docker build -t credit-score-api .` → imagem `python:3.13-slim`, usuário não-root, `HEALTHCHECK` em
`/health`, porta via `PORT` (8082), 1 worker. Em push na `main` o CI publica
`ghcr.io/murilozgodoy/erp-banking-credit-score-api:latest` e `:<sha>`.
