# ERP Banking - Credit Score API

Serviço de cálculo e histórico de score de crédito. Usa padrões **Strategy** (algoritmos de score) e **Factory** (seleção de strategy).

## Requisitos

- Python 3.11+
- Onboarding API rodando (para chamadas reais). Nos testes é mockado.

## Instalação

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
```

## Executar

```bash
uvicorn app.main:app --reload --port 8002
```

Docs: http://localhost:8002/docs

## Testes com cobertura

```bash
pytest
```

Cobertura mínima: **80%**.

## Configuração (`.env`)

- `DATABASE_URL` — padrão SQLite; troque para Postgres em produção
- `ONBOARDING_API_URL` — padrão `http://localhost:8001`

## Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/health` | Health check |
| POST | `/scores/evaluations` | Roda uma avaliação (consulta Onboarding, aplica strategy, persiste) |
| GET | `/scores/customers/{id}/latest` | Retorna a avaliação mais recente do cliente |

### Strategies disponíveis

- `weighted` (padrão): renda 40% + patrimônio 30% + comprometimento 30%
- `simple`: regras binárias de renda/dívidas/comprometimento

## Autenticação Auth0 (Etapa 3)

Envie o **access token** em `Authorization: Bearer <token>`.
A API valida assinatura RS256 via JWKS, issuer HTTPS com barra final,
audience e expiração. `/health` permanece público; preflight CORS não exige token.
Sem token ou com token inválido: **401**, com `WWW-Authenticate: Bearer`.
Roles/permissões por rota ainda não são exigidas; não há uma regra de 403 por escopo nesta etapa.

Configure `AUTH0_DOMAIN` (somente hostname, sem https:// nem barra final) e
`AUTH0_AUDIENCE` (Identifier da API no Auth0). O valor `https://erp-banking-api`
no exemplo precisa ser confirmado no painel e deve coincidir com `VITE_AUTH0_AUDIENCE`.
Não use ID token ou token opaco. Nenhuma credencial de client secret é necessária para validar JWT.

CORS permite `http://localhost:5173`, os métodos utilizados e os headers
`Authorization` e `Content-Type`. Configure `CORS_ORIGINS` para produção.

Os testes usam chaves RSA e tokens locais ou autenticação simulada, sem chamadas ao Auth0.

**Pendente de decisão:** as chamadas Score → Onboarding e Transactions → Score
precisam de propagação do token do usuário ou Machine-to-Machine. Os clients atuais
ainda não enviam credenciais: esses fluxos entre serviços retornarão erro até a escolha ser implementada.

Python carrega `.env` automaticamente. `CORS_ORIGINS` usa JSON, por exemplo
`["http://localhost:5173","https://app.example.com"]`. Sem configuração Auth0,
requisições com Bearer recebem 503; nenhuma rota de negócio é liberada.
`/docs`, `/redoc` e `/openapi.json` também exigem Bearer (abra com um cliente que envie o header).
