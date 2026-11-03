"""Pool configurável para Postgres gerenciado (Aiven: poucas conexões e SSL obrigatório)."""
from sqlalchemy import create_engine

from app.database import _engine_kwargs

PG_URL = (
    "postgresql+psycopg://u:p@db.example.aivencloud.com:12345/defaultdb"
    "?sslmode=require&options=-csearch_path%3Dcredit_score"
)


def test_sqlite_keeps_thread_flag_and_no_pool_args():
    assert _engine_kwargs("sqlite:///./x.db") == {"connect_args": {"check_same_thread": False}}


def test_postgres_defaults(monkeypatch):
    for name in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_RECYCLE"):
        monkeypatch.delenv(name, raising=False)
    assert _engine_kwargs(PG_URL) == {
        "pool_size": 5,
        "max_overflow": 5,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
    }


def test_postgres_env_overrides_and_bad_values_fall_back(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "2")
    monkeypatch.setenv("DB_POOL_RECYCLE", "abc")
    kwargs = _engine_kwargs(PG_URL)
    assert kwargs["pool_size"] == 3 and kwargs["max_overflow"] == 2
    assert kwargs["pool_recycle"] == 1800


def test_url_with_sslmode_and_search_path_reaches_psycopg(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    engine = create_engine(PG_URL, **_engine_kwargs(PG_URL))  # não conecta
    try:
        _, params = engine.dialect.create_connect_args(engine.url)
        assert params["sslmode"] == "require"
        assert params["options"] == "-csearch_path=credit_score"
        assert engine.pool.size() == 3
    finally:
        engine.dispose()
