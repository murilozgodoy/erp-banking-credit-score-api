import re

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {}


engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_SEARCH_PATH = re.compile(r"search_path(?:%3D|=)([A-Za-z_][A-Za-z0-9_]*)")


def schema_from_url(url: str) -> str | None:
    """Schema do `search_path` informado em `options` da URL do PostgreSQL (se houver)."""
    if not url.startswith("postgresql"):
        return None
    match = _SEARCH_PATH.search(url)
    return match.group(1) if match else None


def init_db(bind=None) -> None:
    """Garante o schema (PostgreSQL com search_path) e cria as tabelas nele."""
    bind = bind or engine
    schema = schema_from_url(str(bind.url.render_as_string(hide_password=False)))
    if schema:
        with bind.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    Base.metadata.create_all(bind=bind)
