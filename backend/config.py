"""Backend configuration — loaded from environment variables."""

from pydantic_settings import BaseSettings


class Config(BaseSettings):
    supabase_url: str
    supabase_service_key: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 72

    anthropic_api_key: str
    groq_api_key: str = ""

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # Per-account monthly API budget (in USD)
    monthly_budget_cap: float = 50.0
    budget_warning_pct: float = 0.8

    class Config:
        env_file = ".env"
