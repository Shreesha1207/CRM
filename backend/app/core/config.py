from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-in-production-please-use-a-long-random-value"


class Settings(BaseSettings):
    """Process-level configuration read from the environment.

    Business rules (booking notice, buffers, waitlists...) are *not* here: they
    live in the `settings` table so administrators can change them at runtime.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_name: str = "Booking Platform"
    environment: str = "development"

    database_url: str = "postgresql+psycopg://booking:booking@localhost:5432/booking"

    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 12
    password_reset_ttl_minutes: int = 30

    # Cookie auth is used by the browser client; bearer tokens by API clients.
    cookie_secure: bool = False
    cookie_domain: str | None = None

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    frontend_url: str = "http://localhost:5173"

    # Notifications: "console" logs e-mails, "smtp" sends them.
    email_backend: str = "console"
    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "no-reply@example.com"

    run_background_jobs: bool = True
    job_interval_seconds: int = 60

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @field_validator("database_url")
    @classmethod
    def _use_psycopg(cls, url: str) -> str:
        # Hosted databases (Railway, Render, Heroku...) hand out plain postgres:// URLs,
        # which SQLAlchemy would open with psycopg2; the installed driver is psycopg 3.
        scheme, sep, rest = url.partition("://")
        if sep and scheme in ("postgres", "postgresql"):
            return f"postgresql+psycopg://{rest}"
        return url

    @model_validator(mode="after")
    def _safe_for_production(self) -> "Settings":
        if self.is_production:
            if self.jwt_secret == DEFAULT_JWT_SECRET or len(self.jwt_secret) < 32:
                raise ValueError("Set JWT_SECRET to a random value of at least 32 characters in production")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production (serve the app over HTTPS)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
