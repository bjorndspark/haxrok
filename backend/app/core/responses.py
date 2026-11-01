"""Response envelope contract.

Every JSON endpoint returns one of these shapes — clients get a single parse
path: check `success`, read `data` or `error`.

  success            SuccessResponse[T]   {"success": true, "data": ...}
  paginated list     PaginatedResponse[T] {"success": true, "data": [...],
                                           "pagination": {...}}
  error              ErrorResponse        {"success": false, "error": {...},
                                           "request_id": ...}
  validation error   ValidationErrorResponse — ErrorResponse plus per-field
                                           `errors` items

Non-JSON responses (SSE streams, file mounts) are exempt — they're protocols,
not responses.
"""

from pydantic import BaseModel, Field


class SuccessResponse[T](BaseModel):
    success: bool = Field(True, description="Always true for success responses")
    data: T = Field(..., description="Response payload")


class ErrorDetail(BaseModel):
    code: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable error message")
    field: str | None = Field(None, description="Field that caused the error, if applicable")


class ErrorResponse(BaseModel):
    success: bool = Field(False, description="Always false for error responses")
    error: ErrorDetail = Field(..., description="Error details")
    request_id: str | None = Field(None, description="Correlation ID from X-Request-ID")


class ValidationErrorItem(BaseModel):
    loc: list[str] = Field(..., description="Field path that failed validation")
    msg: str = Field(..., description="Validation error message")
    type: str = Field(..., description="Error type identifier")


class ValidationErrorResponse(BaseModel):
    success: bool = Field(False, description="Always false for error responses")
    error: ErrorDetail = Field(
        default_factory=lambda: ErrorDetail(code="VALIDATION_ERROR", message="Validation failed"),
        description="Error details",
    )
    errors: list[ValidationErrorItem] = Field(
        default_factory=list, description="List of validation errors"
    )
    request_id: str | None = Field(None, description="Correlation ID from X-Request-ID")


class PaginationMeta(BaseModel):
    page: int = Field(..., ge=1, description="Current page number")
    per_page: int = Field(..., ge=1, le=100, description="Items per page")
    total: int = Field(..., ge=0, description="Total item count")
    total_pages: int = Field(..., ge=0, description="Total page count")


class PaginatedResponse[T](BaseModel):
    success: bool = Field(True, description="Always true for success responses")
    data: list[T] = Field(..., description="List of items")
    pagination: PaginationMeta = Field(..., description="Pagination metadata")


def ok[T](data: T) -> SuccessResponse[T]:
    """Wrap a payload in the success envelope — `return ok(model)` in routers."""
    return SuccessResponse[T](data=data)
