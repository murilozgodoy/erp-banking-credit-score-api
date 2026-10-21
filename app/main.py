from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html

from app.auth import require_auth
from app.config import settings

from app.database import Base, engine
from app.routers import health, scores


def create_app() -> FastAPI:
    Base.metadata.create_all(bind=engine)
    app = FastAPI(
        title="ERP Banking - Credit Score API",
        version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None,
        description="Cálculo e histórico de score de crédito",
    )
    app.include_router(health.router)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(scores.router, dependencies=[Depends(require_auth)])

    @app.get("/openapi.json", dependencies=[Depends(require_auth)], include_in_schema=False)
    def openapi():
        return app.openapi()

    @app.get("/docs", dependencies=[Depends(require_auth)], include_in_schema=False)
    def docs():
        return get_swagger_ui_html(openapi_url="/openapi.json", title=app.title)

    @app.get("/redoc", dependencies=[Depends(require_auth)], include_in_schema=False)
    def redoc():
        return get_redoc_html(openapi_url="/openapi.json", title=app.title)

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=True)
