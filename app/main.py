from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from prometheus_fastapi_instrumentator import Instrumentator

from app import models  # noqa: F401  (registra as tabelas no metadata)
from app.auth import require_auth
from app.config import settings
from app.logging_config import RequestIDMiddleware, configure_logging
from app.database import SessionLocal, init_db
from app.messaging.consumer import Binding, EventConsumer
from app.messaging.handlers import handle_anamnesis_updated, handle_credit_decided
from app.routers import analysis, health, scores

BINDINGS = [
    Binding("credit-score.credit-decided", "credit.decided", handle_credit_decided),
    Binding(
        "credit-score.anamnesis-updated", "customer.anamnesis_updated", handle_anamnesis_updated
    ),
]


def build_consumer() -> EventConsumer:
    return EventConsumer(settings.rabbitmq_url, BINDINGS, SessionLocal)


@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer = build_consumer()
    consumer.start()
    try:
        yield
    finally:
        await consumer.stop()


def create_app() -> FastAPI:
    configure_logging(settings.log_level)
    init_db()
    root_path = settings.root_path.rstrip("/")
    app = FastAPI(
        title="ErpIa - Credit Score API",
        version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None,
        description="ErpIa: score de crédito, histórico e análise do negócio",
        root_path=root_path,
        servers=[{"url": root_path}] if root_path else None,
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIDMiddleware)  # mais externo: cobre CORS, auth e erros
    app.include_router(scores.router, dependencies=[Depends(require_auth)])
    app.include_router(analysis.router, dependencies=[Depends(require_auth)])
    Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(
        app, endpoint="/metrics", include_in_schema=False
    )

    openapi_url = f"{root_path}/openapi.json"

    @app.get("/openapi.json", dependencies=[Depends(require_auth)], include_in_schema=False)
    def openapi():
        return app.openapi()

    @app.get("/docs", dependencies=[Depends(require_auth)], include_in_schema=False)
    def docs():
        return get_swagger_ui_html(openapi_url=openapi_url, title=app.title)

    @app.get("/redoc", dependencies=[Depends(require_auth)], include_in_schema=False)
    def redoc():
        return get_redoc_html(openapi_url=openapi_url, title=app.title)

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=True)
