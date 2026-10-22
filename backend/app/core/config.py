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
    # How long an invited customer has to register and accept a booking.
    invitation_ttl_days: int = 7

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

    # Booking assistant. One Groq key serves both the model that talks to the customer
    # and the guards; any OpenAI-compatible server (e.g. Ollama) works as the base URL.
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    chat_model: str = "openai/gpt-oss-120b"
    # Sent as reasoning_effort (low / medium / high); empty to leave it out.
    chat_reasoning_effort: str = "low"
    chat_max_output_tokens: int = 2048
    chat_max_tool_rounds: int = 8
    chat_timeout_seconds: float = 60.0
    # Only the last N customer turns are sent to the model, to stay inside token limits.
    chat_history_turns: int = 8
    # The policy guard reads a written policy and answers ALLOW or BLOCK.
    guard_topic_model: str = "openai/gpt-oss-20b"
    guard_reasoning_effort: str = "low"
    # Prompt Guard, the cheap first filter for jailbreak wording; empty switches it off.
    guard_injection_model: str = "meta-llama/llama-prompt-guard-2-86m"
    guard_injection_threshold: float = 0.8
    guard_timeout_seconds: float = 10.0
    # How long a booking the assistant made stays held for the customer to confirm.
    chat_hold_minutes: int = 10
    chat_max_open_holds: int = 2
    # Abuse and cost limits, per customer.
    chat_max_input_chars: int = 1000
    chat_max_reply_chars: int = 800
    chat_messages_per_minute: int = 10
    chat_daily_message_limit: int = 60
    chat_daily_token_limit: int = 500_000
    chat_max_messages_per_conversation: int = 40
    chat_strike_limit: int = 3
    chat_strike_window_minutes: int = 10
    chat_pause_minutes: int = 60
    # Contacting the admin from the chat: the reply customers are promised, and how often they can ask.
    chat_help_response_hours: int = 6
    chat_help_requests_per_week: int = 3

    @property
    def chat_configured(self) -> bool:
        return bool(self.groq_api_key and self.chat_model and self.guard_topic_model)

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
