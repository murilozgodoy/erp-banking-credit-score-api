from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./credit_score.db"
    onboarding_api_url: str = "http://localhost:8001"
    log_level: str = "INFO"
    app_name: str = "erp-banking-credit-score-api"
    auth0_domain: str = ""
    auth0_audience: str = ""
    cors_origins: list[str] = ["http://localhost:5173"]


settings = Settings()
