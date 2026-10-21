# ERP Banking - Credit Score API

Serviço de cálculo e histórico de score de crédito. Usa padrões **Strategy** (algoritmos de score) e **Factory** (seleção de strategy).
Documentação central do projeto em [erp-banking-front/docs](https://github.com/murilozgodoy/erp-banking-front/tree/main/docs).

## Requisitos

- Python 3.11+
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

## Testes com cobertura

```bash
pytest
```

Cobertura mínima: **80%**.

## Configuração (`.env`)

| Variável             | Padrão                        | Descrição                                                   |
| -------------------- | ----------------------------- | ----------------------------------------------------------- |
| `DATABASE_URL`       | `sqlite:///./credit_score.db` | Em produção, PostgreSQL (`postgresql+psycopg://...`)        |
| `ONBOARDING_API_URL` | `http://localhost:8081`       | URL da Onboarding API                                       |
| `PORT`               | `8082`                        | Porta usada por `python -m app.main` (porta aberta na EC2)  |
| `AUTH0_DOMAIN`       | —                             | Domínio do tenant, sem `https://`                           |
| `AUTH0_AUDIENCE`     | —                             | Identifier da API no Auth0: `https://erp-banking-api`       |
| `CORS_ORIGINS`       | `["http://localhost:5173"]`   | Origens do frontend, em JSON                                |

## Endpoints

| Método | Rota                                  | Permissão      | Descrição                                                          |
| ------ | ------------------------------------- | -------------- | ------------------------------------------------------------------ |
| GET    | `/health`                             | pública        | Health check                                                       |
| POST   | `/scores/evaluations`                 | `write:scores` | Roda uma avaliação (consulta o Onboarding, aplica a strategy, persiste) |
| GET    | `/scores/evaluations?customer_id=`    | `read:scores`  | Histórico de avaliações do cliente (mais recente primeiro)         |
| GET    | `/scores/customers/{id}/latest`       | `read:scores`  | Avaliação vigente do cliente (usada pela Transactions API)         |
| GET    | `/scores/strategies`                  | `read:scores`  | Strategies registradas na `ScoreStrategyFactory`                   |

### Strategies disponíveis

- `weighted` (padrão): renda 40% + patrimônio 30% + comprometimento 30%
- `simple`: regras binárias de renda/dívidas/comprometimento

## Autenticação e autorização (Auth0)

Envie o **access token** do Auth0 em `Authorization: Bearer <token>`. A API valida assinatura
RS256 via JWKS, issuer, audience e expiração: **401** sem token ou com token inválido, **403**
quando falta a permissão da rota (claim `permissions` do RBAC do Auth0, ou `scope`) e **503** se
`AUTH0_*` não estiver configurado. `/health` é público; `/docs`, `/redoc` e `/openapi.json` exigem token.

**Token relay:** ao calcular um score, o `OnboardingClient` repassa o mesmo token ao Onboarding,
que aplica as próprias permissões. Por isso calcular score exige `write:scores` aqui **e**
`read:customers` no Onboarding; se faltar a segunda, a resposta é 403 com o motivo.

Os testes usam chaves RSA e tokens locais ou autenticação simulada, sem chamadas ao Auth0.
