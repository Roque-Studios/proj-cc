import structlog
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

logger = structlog.get_logger()

load_dotenv()

_INSECURE_SECRET_KEY_PLACEHOLDER = "change_this_in_production_please"


class Settings(BaseSettings):
    # Required — non-empty so the app fails fast (with a clear error) if any is
    # missing OR set to an empty string (e.g. an unset compose interpolation).
    DATABASE_URL: str = Field(min_length=1)
    REDIS_URL: str = Field(min_length=1)
    SECRET_KEY: str = Field(min_length=1)
    CC_VERSION: str = Field(min_length=1)

    # Optional with defaults
    ENVIRONMENT: str = "dev"
    ALLOWED_ORIGINS: str = "http://localhost,http://localhost:80,http://127.0.0.1"
    BROKER_CONNECTION_RETRY_ON_STARTUP: bool = True
    SENTRY_DSN: str | None = None
    LOG_LEVEL: str = "INFO"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 21600
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080
    DEFAULT_ADMIN_EMAIL: str = "admin@admin.com"
    DEFAULT_ADMIN_PASSWORD: str = "admin"

    # Celery (empty values fall back to REDIS_URL on dedicated Redis DBs, see properties below)
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""

    # Watermarked media cache (Redis)
    WATERMARK_CACHE_REDIS_URL: str = ""
    WATERMARK_CACHE_TTL_SECONDS: int = 3600

    # JWT token revocation list (Redis; defaults to REDIS_URL, DB 3)
    TOKEN_REVOCATION_REDIS_URL: str = ""

    # Payment gateway (mock | stripe | paypal). "mock" is the default so the
    # stack runs with zero credentials; switching gateways is a config change
    # only (see app/payments/factory.py).
    PAYMENT_PROVIDER: str = "mock"
    # Stripe credentials (required when PAYMENT_PROVIDER=stripe)
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_API_BASE: str = "https://api.stripe.com/v1"
    # PayPal credentials (required when PAYMENT_PROVIDER=paypal)
    PAYPAL_CLIENT_ID: str = ""
    PAYPAL_CLIENT_SECRET: str = ""
    PAYPAL_WEBHOOK_ID: str = ""
    # sandbox | live — selects the PayPal REST API base URL
    PAYPAL_ENVIRONMENT: str = "sandbox"
    # Optional existing catalog product (PROD-...) for billing plans; when
    # empty the bootstrap script (app.payments.bootstrap_paypal) creates one.
    PAYPAL_PRODUCT_ID: str = ""
    # Wompi (El Salvador) credentials — required when PAYMENT_PROVIDER=wompi.
    # Wompi SV authenticates with OAuth2 client credentials (App ID / API
    # Secret). The environment is per-app (each applicativo is marked
    # productivo or not in the panel) rather than a URL switch; the URLs are
    # overridable for test accounts.
    WOMPI_CLIENT_ID: str = ""
    WOMPI_CLIENT_SECRET: str = ""
    WOMPI_ENVIRONMENT: str = "sandbox"
    WOMPI_API_BASE_URL: str = "https://api.wompi.sv"
    WOMPI_TOKEN_URL: str = "https://id.wompi.sv/connect/token"
    # Recurring subscriptions charge the subscriber on this day of each month.
    WOMPI_DIA_DE_PAGO: int = 1
    # Where the customer returns after completing a 3DS one-time charge.
    WOMPI_3DS_REDIRECT_URL: str = ""

    # Single subscription tier (monthly). The plan id is the gateway price id
    # (e.g. a Stripe recurring price); the price is informational for the API.
    SUBSCRIPTION_TIER_PLAN_ID: str = "price_monthly_tier"
    SUBSCRIPTION_TIER_PRICE_CENTS: int = 500

    # Payment-failure notifications (SMTP). All optional: when SMTP_HOST is
    # empty the notify task degrades to a structured log (dev/mock setups).
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_TLS: bool = True

    # Photo-post media uploads (validated images). Original (unwatermarked)
    # uploads live in a PRIVATE store — never served directly to clients; only
    # internal service code can read them. Serving watermarks originals on the
    # fly per viewer (see app.watermark), so no served copy is persisted.
    ORIGINAL_MEDIA_STORAGE_PATH: str = "/data/media/original"
    MAX_MEDIA_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB per file
    ALLOWED_MEDIA_EXTENSIONS: str = ".jpg,.jpeg,.png,.webp,.gif"

    @property
    def allowed_media_extensions(self) -> set[str]:
        """Parsed set of allowed upload extensions (lowercased)."""
        return {
            e.strip().lower()
            for e in self.ALLOWED_MEDIA_EXTENSIONS.split(",")
            if e.strip()
        }

    @property
    def cors_origins(self) -> list[str]:
        """Parse ALLOWED_ORIGINS (comma-separated) into a list."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    def _redis_db_url(self, db_index: int) -> str:
        """Rewrite the DB index of REDIS_URL (e.g. redis://host:6379/0 -> /1)."""
        base = self.REDIS_URL.rsplit("/", 1)[0]
        return f"{base}/{db_index}"

    @property
    def celery_broker_url(self) -> str:
        """Celery broker URL (defaults to REDIS_URL, DB 0)."""
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def celery_result_backend(self) -> str:
        """Celery result backend URL (defaults to REDIS_URL, DB 1)."""
        return self.CELERY_RESULT_BACKEND or self._redis_db_url(1)

    @property
    def watermark_cache_redis_url(self) -> str:
        """Redis URL for the watermarked media cache (defaults to REDIS_URL, DB 2)."""
        return self.WATERMARK_CACHE_REDIS_URL or self._redis_db_url(2)

    @property
    def token_revocation_redis_url(self) -> str:
        """Redis URL for the JWT revocation list (defaults to REDIS_URL, DB 3)."""
        return self.TOKEN_REVOCATION_REDIS_URL or self._redis_db_url(3)

    class Config:
        env_file = ".env"
        extra = "ignore"


def get_settings() -> Settings:
    try:
        logger.info("Loading application settings")
        settings = Settings()  # Pydantic will validate and raise if missing
        logger.info("Settings loaded successfully")

        # Reject blank/whitespace-only secrets (Field(min_length=1) still allows "   ").
        if not settings.SECRET_KEY.strip():
            raise RuntimeError(
                "SECRET_KEY must not be empty or whitespace. "
                'Generate a strong key with: python -c "import secrets; print(secrets.token_hex(32))" '
                "and set it in your environment before starting."
            )

        # Startup security check: reject insecure SECRET_KEY in production.
        if settings.ENVIRONMENT == "prod":
            if settings.SECRET_KEY == _INSECURE_SECRET_KEY_PLACEHOLDER:
                raise RuntimeError(
                    "SECRET_KEY is set to the insecure placeholder value. "
                    'Generate a strong key with: python -c "import secrets; print(secrets.token_hex(32))" '
                    "and set it in your production environment before starting."
                )
        elif settings.SECRET_KEY == _INSECURE_SECRET_KEY_PLACEHOLDER:
            logger.warning(
                "SECRET_KEY is using the insecure placeholder value — "
                "set a strong random key before deploying to production."
            )

        return settings
    except RuntimeError:
        raise
    except Exception as e:
        logger.error("Configuration error", error=str(e))
        raise RuntimeError(
            f"Configuration error: {e}\n"
            "Make sure all required env vars are set (see .env.example)"
        )


settings = get_settings()
