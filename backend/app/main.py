from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.core.clock import get_clock
from app.core.config import settings
from app.core.errors import (
    AppError,
    app_error_handler,
    http_exception_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.logging import get_logger, setup_logging
from app.core.middleware import RequestLoggingMiddleware
from app.core.responses import SuccessResponse, ok
from app.core.storage import MEDIA_URL_PREFIX
from app.features.chat.router import router as chat_router
from app.features.logs.router import router as logs_router
from app.features.speech.router import router as speech_router

logger = get_logger(__name__)

health_router = APIRouter()


class HealthResponse(BaseModel):
    status: str = Field(..., description="Service health status")
    timestamp: datetime = Field(..., description="Current server timestamp")
    version: str = Field(..., description="API version")


@health_router.get("/health", tags=["health"])
def health() -> SuccessResponse[HealthResponse]:
    return ok(HealthResponse(status="ok", timestamp=get_clock().now(), version="0.1.0"))


def create_app() -> FastAPI:
    setup_logging()
    logger.info("Starting %s (env=%s)", settings.PROJECT_NAME, settings.ENVIRONMENT)

    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="0.1.0",
        openapi_url="/openapi.json",
        docs_url="/docs",
    )

    # Added first = innermost: CORS (added after) wraps it, so even the 500
    # envelope this middleware emits carries CORS headers the browser needs
    # to read it.
    app.add_middleware(RequestLoggingMiddleware)

    if settings.cors_origins:
        logger.info("CORS origins: %s", settings.cors_origins)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        logger.warning("No CORS origins configured; frontend will be blocked")

    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(health_router, prefix=settings.API_V1_PREFIX)
    app.include_router(chat_router, prefix=settings.API_V1_PREFIX)
    app.include_router(logs_router, prefix=settings.API_V1_PREFIX)
    app.include_router(speech_router, prefix=settings.API_V1_PREFIX)
    logger.info("Routers registered under %s: health, chat, logs, speech", settings.API_V1_PREFIX)

    # Generated media (images, speech) is served straight from MEDIA_DIR.
    Path(settings.MEDIA_DIR).mkdir(parents=True, exist_ok=True)
    app.mount(
        MEDIA_URL_PREFIX,
        StaticFiles(directory=settings.MEDIA_DIR, check_dir=False),
        name="media-files",
    )
    return app


app = create_app()
