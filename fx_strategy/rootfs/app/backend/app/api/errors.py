"""Domain errors and their HTTP representations."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.logging_setup import get_logger
from app.money import MoneyError

log = get_logger(__name__)


class DomainError(Exception):
    """A rule of the application was violated."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "domain_error"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class ValidationError(DomainError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"


class ProviderError(DomainError):
    """An upstream rate or Wise provider failed.

    Provider failures are surfaced, never swallowed: the UI must be able to show
    that a refresh did not succeed.
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "provider_error"


class FeatureDisabledError(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "feature_disabled"


def _payload(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(MoneyError)
    async def _money(_request: Request, exc: MoneyError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_payload("invalid_amount", str(exc)),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # The message is logged in full but not returned: tracebacks can carry
        # connection strings and other detail that should not reach a browser.
        log.exception("unhandled_error", path=request.url.path, error=type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_payload("internal_error", "An unexpected error occurred."),
        )
