from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./credit_score.db"
    onboarding_api_url: str = "http://localhost:8081"
    # Cache de GET /me do Onboarding por sub: mudanças de permissão de membro levam até esse tempo para valer.
    me_cache_ttl_seconds: float = 60.0
    log_level: str = "INFO"
    app_name: str = "erp-banking-credit-score-api"
    auth0_domain: str = ""
    auth0_audience: str = ""
    cors_origins: list[str] = ["http://localhost:5173"]
    port: int = 8082
    root_path: str = ""
    rabbitmq_url: str = ""
    # Análise do negócio por LLM (opcional). Chave vazia = só regras.
    anthropic_api_key: str = ""
    analysis_model: str = "claude-sonnet-5-5"
    analysis_timeout_seconds: float = 20.0
    # Assistente ErpIa: ferramentas leem o Transactions com o token do cliente (token relay).
    transactions_api_url: str = "http://transactions-api:8080"
    # Histórico do Assistente: conversas apagadas (soft delete) somem de vez depois de N dias.
    assistant_retention_days: int = 30
    assistant_purge_interval_hours: float = 6.0


settings = Settings()
