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
