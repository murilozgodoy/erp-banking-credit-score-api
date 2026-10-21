import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.auth import Principal, require_auth

ALL_PERMISSIONS = frozenset({"read:scores", "write:scores"})


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db_session(engine):
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(engine):
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_auth] = lambda: Principal(
        token="test-token", subject="auth0|test", permissions=ALL_PERMISSIONS
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def onboarding_response():
    return {
        "id": 1,
        "nome": "João",
        "cpf": "12345678900",
        "email": "joao@example.com",
        "telefone": "11999",
        "created_at": "2026-01-01T00:00:00",
        "address": None,
        "financial_profile": {
            "id": 1,
            "renda_mensal": 8000.0,
            "patrimonio": 50000.0,
            "ocupacao": "CLT",
        },
        "anamnesis": {
            "id": 1,
            "dividas_ativas": 1500.0,
            "comprometimento_renda": 25.0,
            "updated_at": "2026-01-02T00:00:00",
        },
    }
