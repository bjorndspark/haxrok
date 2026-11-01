"""Error contract: AppError subclasses carry status + machine-readable code;
handlers emit the ErrorResponse / ValidationErrorResponse envelope from
app.core.responses ({"success": false, "error": {...}, "request_id": ...})."""

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger, get_request_id
from app.core.responses import (
    ErrorDetail,
    ErrorResponse,
    ValidationErrorItem,
    ValidationErrorResponse,
)

logger = get_logger(__name__)


class AppError(Exception):
    """Base error for application-specific exceptions."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        detail: str,
        error_code: str | None = None,
        field: str | None = None,
    ):
        self.detail = detail
        self.field = field
        if error_code:
            self.error_code = error_code
        super().__init__(detail)


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "NOT_FOUND"


class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "BAD_REQUEST"


class UpstreamError(AppError):
    """The AI gateway or another upstream dependency failed."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "UPSTREAM_ERROR"


def _body(code: str, message: str, field: str | None = None) -> dict:
    return ErrorResponse(
        error=ErrorDetail(code=code, message=message, field=field),
        request_id=get_request_id(),
    ).model_dump()


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.warning(
        "AppError %s: %s (%s %s)",
        exc.error_code,
        exc.detail,
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_body(exc.error_code, exc.detail, field=exc.field),
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        429: "RATE_LIMITED",
    }.get(exc.status_code, f"HTTP_{exc.status_code}")
    logger.warning(
        "HTTPException %d: %s (%s %s)",
        exc.status_code,
        exc.detail,
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_body(code, str(exc.detail)),
        headers=getattr(exc, "headers", None),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.warning("ValidationError: %s (%s %s)", exc.errors(), request.method, request.url.path)
    body = ValidationErrorResponse(
        errors=[
            ValidationErrorItem(loc=[str(p) for p in e["loc"]], msg=e["msg"], type=e["type"])
            for e in exc.errors()
        ],
        request_id=get_request_id(),
    )
    return JSONResponse(status_code=422, content=body.model_dump())


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled %s: %s (%s %s)",
        type(exc).__name__,
        exc,
        request.method,
        request.url.path,
    )
    return JSONResponse(status_code=500, content=_body("INTERNAL_ERROR", "Internal server error"))
