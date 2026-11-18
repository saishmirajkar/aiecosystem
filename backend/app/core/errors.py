"""Domain errors and their mapping to the consistent API error envelope."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("above.errors")


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details
        if code:
            self.code = code


class AuthenticationRequired(AppError):
    status_code, code = 401, "authentication_required"


class InvalidCredentials(AppError):
    status_code, code = 401, "invalid_credentials"


class AccountLocked(AppError):
    status_code, code = 423, "account_locked"


class PermissionDenied(AppError):
    status_code, code = 403, "permission_denied"


class NotFound(AppError):
    status_code, code = 404, "not_found"


class Conflict(AppError):
    status_code, code = 409, "conflict"


class InvalidState(AppError):
    status_code, code = 409, "invalid_state"


class BusinessRuleViolation(AppError):
    status_code, code = 409, "business_rule_violation"


class InsufficientStock(AppError):
    status_code, code = 409, "insufficient_stock"


class ValidationFailed(AppError):
    status_code, code = 422, "validation_error"


class RateLimited(AppError):
    status_code, code = 429, "rate_limited"


def _envelope(request: Request, status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details, "request_id": request_id}},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return _envelope(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return _envelope(request, 422, "validation_error", "Request validation failed", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed", 401: "authentication_required"}.get(exc.status_code, "http_error")
        return _envelope(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error (request_id=%s)", getattr(request.state, "request_id", None))
        return _envelope(request, 500, "internal_error", "An unexpected error occurred")
