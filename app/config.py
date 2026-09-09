from pydantic_settings import BaseSettings
from functools import lru_cache
from urllib.parse import urlparse


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/qr_reviews"

    @property
    def async_database_url(self) -> str:
        # Heroku gives us postgres:// but asyncpg needs postgresql+asyncpg://
        if self.DATABASE_URL.startswith("postgres://"):
            return self.DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
        elif self.DATABASE_URL.startswith("postgresql://"):
            return self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
        return self.DATABASE_URL

    # Google Gemini AI
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"

    # JWT Authentication
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 60 * 24 * 7  # 7 days

    # App
    APP_URL: str = "http://localhost:8000"
    ENVIRONMENT: str = "development"
    ALLOWED_HOSTS: str = ""
    MAX_LOGO_BYTES: int = 2 * 1024 * 1024

    @property
    def cookie_secure(self) -> bool:
        return self.APP_URL.lower().startswith("https://")

    @property
    def allowed_hosts(self) -> list[str]:
        configured = [host.strip() for host in self.ALLOWED_HOSTS.split(",") if host.strip()]
        app_host = urlparse(self.APP_URL).hostname
        defaults = ["localhost", "127.0.0.1", "testserver"]
        if app_host:
            defaults.append(app_host)
        return list(dict.fromkeys(configured + defaults))

    # Initial super-admin seed. Leave ADMIN_PASSWORD empty after the account
    # exists so application restarts never reset its password.
    ADMIN_NAME: str = "Admin User"
    ADMIN_EMAIL: str = "aurionstack@gmail.com"
    ADMIN_PASSWORD: str = ""
    ADMIN_SLUG: str = "admin"

    # Razorpay Payment
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""
    ANNUAL_PRICE_PAISE: int = 99900
    TWO_YEAR_PRICE_PAISE: int = 159900
    PHYSICAL_STAND_PRICE_PAISE: int = 24900

    # Rate Limiting (AI endpoint)
    AI_RATE_LIMIT: str = "5/minute"  # per scan/IP

    # Email / SMTP Settings
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "noreply@qrreviews.app"
    SMTP_TLS: bool = True

    EMAIL_OTP_EXPIRY_MINUTES: int = 10
    EMAIL_OTP_RESEND_SECONDS: int = 60

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }



@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
