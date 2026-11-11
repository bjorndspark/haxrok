from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Process-wide configuration, sourced from .env + real env vars."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )

    PROJECT_NAME: str = "App"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    # Bind-mounted to ./logs on the host. Empty = stdout only.
    LOG_FILE: str = "logs/backend/app.log"
    # Browsers can't write files, so the frontend ships batched console/error
    # logs to POST /api/v1/logs/frontend and the backend appends them here.
    # Empty = the endpoint accepts and discards.
    FRONTEND_LOG_FILE: str = "logs/frontend/app.log"

    # Host-run default reaches the shared infra Redis on its machine-level
    # port; compose overrides it with redis://redis:6379/0.
    REDIS_URL: str = "redis://localhost:56379/0"
    BACKEND_CORS_ORIGINS: str = ""

    # Any OpenAI-compatible endpoint: Meta's API (what .env.example sets),
    # a gateway such as LiteLLM or Bifrost, or empty for api.openai.com.
    # Provider keys, routing, and budgets live behind that endpoint.
    AI_BASE_URL: str = ""
    AI_API_KEY: str = ""
    AI_CHAT_MODEL: str = "gpt-4o-mini"
    # Per-operation timeout (connect, each read) for model calls.
    AI_TIMEOUT_S: float = 120.0
    # Gateway only: optional comma-separated fallback chains (provider/model
    # form), sent as the `fallbacks` request field (Bifrost's format).
    # Setting it overrides the gateway's own fallbacks; leave empty when
    # calling Meta's API directly.
    AI_CHAT_FALLBACKS: str = ""
    # Seconds a model answer stays cached in Redis, keyed by the whole
    # request (model, fallbacks, messages, schema); 0 = always call the model.
    AI_CACHE_TTL_S: int = 7 * 24 * 3600

    # Debug feature. The analysis call streams nothing for ~90 s and a run
    # has taken 130 s, so it gets its own per-read timeout above AI_TIMEOUT_S.
    DEBUG_AI_TIMEOUT_S: float = 300.0
    # kicad-cli must be at least as new as the KiCad that saved the upload.
    KICAD_CLI: str = "kicad-cli"
    KICAD_TIMEOUT_S: float = 60.0
    # ngspice draws a flagged I²C line; missing or failing, the card has no plot.
    NGSPICE: str = "ngspice"
    NGSPICE_TIMEOUT_S: float = 10.0
    # A vendor model's circuit (a switching regulator's start-up) runs for
    # minutes: orbit's TPS63802 takes 10-12 (plan 004 step 6).
    NGSPICE_MODEL_TIMEOUT_S: float = 900.0
    # All of one request's vendor-model runs (plan 006 step 4): a run still
    # queued when this runs out is skipped; one already going finishes.
    SPICE_BUDGET_S: float = 1800.0
    # The bench supply's current limit in the extra start-up run, A (assumed;
    # the review form doesn't ask for it).
    BENCH_CURRENT_LIMIT_A: float = 1.0
    # Seconds a finished vendor-model run stays cached in Redis, keyed by the
    # deck, the model files and ngspice's version; 0 = always run ngspice.
    SPICE_CACHE_TTL_S: int = 30 * 24 * 3600
    # Each IC's datasheet PDF, kept by part number (git-ignored data/ is
    # bind-mounted at /app/data). The timeout is per request; a part may
    # take three. PDFs over the size cap count as not found.
    DATASHEET_DIR: str = "data/datasheets"
    DATASHEET_TIMEOUT_S: float = 10.0
    DATASHEET_MAX_BYTES: int = 30 * 1024 * 1024
    # Each IC's SPICE model files, kept by the same key as its datasheet.
    # Lookups share the datasheet's timeout and size cap.
    SPICE_MODEL_DIR: str = "data/models"
    DEBUG_MAX_FILES: int = 200
    DEBUG_MAX_UPLOAD_BYTES: int = 20 * 1024 * 1024

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.BACKEND_CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
