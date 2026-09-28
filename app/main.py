from fastapi import FastAPI

from app.database import Base, engine
from app.routers import health, scores


def create_app() -> FastAPI:
    Base.metadata.create_all(bind=engine)
    app = FastAPI(
        title="ERP Banking - Credit Score API",
        version="1.0.0",
        description="Cálculo e histórico de score de crédito",
    )
    app.include_router(health.router)
    app.include_router(scores.router)
    return app


app = create_app()
