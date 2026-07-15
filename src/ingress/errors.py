from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorCode(str, Enum):
    invalid_request = "INVALID_REQUEST"
    unauthorized = "UNAUTHORIZED"
    forbidden = "FORBIDDEN"
    conflict = "CONFLICT"
    missing_required_context = "MISSING_REQUIRED_CONTEXT"
    payload_too_large = "PAYLOAD_TOO_LARGE"
    service_unavailable = "SERVICE_UNAVAILABLE"
    orchestrator_timeout = "ORCHESTRATOR_TIMEOUT"
    client_cancelled = "CLIENT_CANCELLED"
    internal_error = "INTERNAL_ERROR"


class ErrorResponse(BaseModel):
    code: ErrorCode
    message: str
    request_id: str | None = None
    trace_id: str | None = None
    details: Any | None = None


class ApiIngressError(Exception):
    status_code = 500
    code = ErrorCode.internal_error
    message = "Internal server error"

    def __init__(
        self,
        message: str | None = None,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
        details: Any | None = None,
    ) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.request_id = request_id
        self.trace_id = trace_id
        self.details = details


class InvalidRequestError(ApiIngressError):
    status_code = 400
    code = ErrorCode.invalid_request
    message = "Invalid request"


class UnauthorizedError(ApiIngressError):
    status_code = 401
    code = ErrorCode.unauthorized
    message = "Unauthorized"


class ForbiddenError(ApiIngressError):
    status_code = 403
    code = ErrorCode.forbidden
    message = "Forbidden"


class ConflictError(ApiIngressError):
    status_code = 409
    code = ErrorCode.conflict
    message = "Conflict"


class MissingRequiredContextError(ApiIngressError):
    status_code = 422
    code = ErrorCode.missing_required_context
    message = "Missing required vet context"


class PayloadTooLargeError(ApiIngressError):
    status_code = 413
    code = ErrorCode.payload_too_large
    message = "Payload too large"


class OrchestratorUnavailableError(ApiIngressError):
    status_code = 503
    code = ErrorCode.service_unavailable
    message = "Orchestrator is unavailable"


class OrchestratorTimeoutError(ApiIngressError):
    status_code = 504
    code = ErrorCode.orchestrator_timeout
    message = "Orchestrator timed out"


def build_error_response(error: ApiIngressError) -> JSONResponse:
    payload = ErrorResponse(
        code=error.code,
        message=error.message,
        request_id=error.request_id,
        trace_id=error.trace_id,
        details=error.details,
    )
    headers = _trace_headers(error.request_id, error.trace_id)
    return JSONResponse(
        status_code=error.status_code,
        content=payload.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


async def api_error_handler(_request: Request, error: ApiIngressError) -> JSONResponse:
    return build_error_response(error)


async def validation_error_handler(
    _request: Request, error: RequestValidationError
) -> JSONResponse:
    body = error.body if isinstance(error.body, dict) else {}
    request_id = _string_or_none(body.get("request_id")) or str(uuid4())
    trace_id = _string_or_none(body.get("trace_id")) or request_id
    details = {"validation_errors": _serializable_errors(error.errors())}

    if _is_missing_context_error(error.errors()):
        return build_error_response(
            MissingRequiredContextError(
                request_id=request_id,
                trace_id=trace_id,
                details=details,
            )
        )

    return build_error_response(
        InvalidRequestError(
            request_id=request_id,
            trace_id=trace_id,
            details=details,
        )
    )


async def unhandled_error_handler(_request: Request, error: Exception) -> JSONResponse:
    return build_error_response(
        ApiIngressError(details={"error_type": type(error).__name__})
    )


def _is_missing_context_error(errors: list[dict[str, Any]]) -> bool:
    required_fields = {"user_id", "session_id", "pet_id"}
    for item in errors:
        loc = tuple(str(part) for part in item.get("loc", ()))
        if "vet_context" not in loc:
            continue
        if loc[-1] in required_fields:
            return True
        if loc[-1] == "vet_context":
            return True
    return False


def _serializable_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serializable: list[dict[str, Any]] = []
    for error in errors:
        item = dict(error)
        if "ctx" in item and isinstance(item["ctx"], dict):
            item["ctx"] = {key: str(value) for key, value in item["ctx"].items()}
        serializable.append(item)
    return serializable


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _trace_headers(request_id: str | None, trace_id: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if request_id:
        headers["X-Request-ID"] = request_id
    if trace_id:
        headers["X-Trace-ID"] = trace_id
    return headers
