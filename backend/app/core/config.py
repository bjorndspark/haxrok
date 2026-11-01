from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
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

    # Host-run default reaches the shared infra postgres on its machine-level
    # port; in Docker the compose service sets the internal DSN instead.
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:55432/app"
    # Same pattern for shared-infra Redis: host-run default hits the
    # machine-level port; compose overrides with redis://redis:6379/0.
    REDIS_URL: str = "redis://localhost:56379/0"
    BACKEND_CORS_ORIGINS: str = ""

    # Any OpenAI-compatible endpoint: Bifrost, LiteLLM, or empty for
    # api.openai.com. Provider keys, routing, and budgets live in the
    # gateway; fallbacks too, unless an AI_*_FALLBACKS var below overrides
    # them per request.
    AI_BASE_URL: str = ""
    AI_API_KEY: str = ""
    AI_CHAT_MODEL: str = "gpt-4o-mini"
    AI_IMAGE_MODEL: str = "gpt-image-1-mini"
    AI_TTS_MODEL: str = "tts-1"
    AI_STT_MODEL: str = "whisper-1"
    # Per-operation timeout (connect, each read) for gateway calls. Image
    # generation is the slowest single read, so keep it well above that.
    AI_TIMEOUT_S: float = 120.0
    # Optional comma-separated fallback chains (provider/model form),
    # forwarded to the gateway as Bifrost's `fallbacks` request field — one
    # per modality. Setting one overrides the gateway's own VK/routing-rule
    # fallbacks for that request type; empty = gateway decides.
    AI_CHAT_FALLBACKS: str = ""
    AI_IMAGE_FALLBACKS: str = ""
    AI_TTS_FALLBACKS: str = ""
    AI_STT_FALLBACKS: str = ""

    # Generated media, bind-mounted to ./data on the host and served at
    # /media/files/<name>.
    MEDIA_DIR: str = "data/media"
    MEDIA_MAX_BYTES: int = 10 * 1024 * 1024

    # Voice mode: optional server-side echo cancellation behind the
    # browser's own getUserMedia AEC ("off" = browser AEC only). "speex"
    # uses libspeexdsp1 in the backend image, "webrtc" uses pywebrtc-audio
    # (WebRTC AEC3; the optional `webrtc` extra). See app/core/echo.py for
    # the alignment model.
    VOICE_ECHO_CANCELLER: Literal["off", "speex", "webrtc"] = "off"
    # Far-end lag seeded as silence (ms), on top of the playback-buffer
    # model — covers fixed network/acoustic delay. Under-estimating is
    # safer than over-estimating: cancellers only look back, not forward.
    VOICE_EC_DELAY_MS: int = 0
    # Speex only: adaptive filter tail (ms) — must exceed the residual
    # delay between the queued reference and the echo in the mic.
    VOICE_EC_FILTER_MS: int = 256

    @field_validator("DATABASE_URL")
    @classmethod
    def _rewrite_postgres_scheme(cls, v: str) -> str:
        # Hosted Postgres URLs are often bare `postgresql://`; the app needs the
        # asyncpg driver. Rewrite exactly the two driver-less prefixes and pass
        # everything else through — `postgresql+asyncpg://` (dev) and
        # `sqlite+aiosqlite://` (tests) must reach the engine untouched.
        for bare in ("postgresql://", "postgres://"):
            if v.startswith(bare):
                return f"postgresql+asyncpg://{v[len(bare) :]}"
        return v

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.BACKEND_CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
