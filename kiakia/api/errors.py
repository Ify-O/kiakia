"""Every error leaves the API as RFC 9457 Problem Details (docs/adr/0002).

Kiakia's own errors are mapped to HTTP status codes here, in ONE place:
- request validation:          400 (query/path/header/malformed body) or 422 (body fields)
- NotFound -> 404, InvalidCursor -> 400
- SourceRejectedRequest -> 502 (the channel refused), SourceUnavailable/StorageUnavailable -> 503
- anything else -> 500 with a generic message; details go to the server log only.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from kiakia.domain.errors import (
    DeliveryUnknown,
    IdempotencyConflict,
    InvalidCursor,
    InvalidInput,
    NotFound,
    RateLimited,
    SourceRejectedRequest,
    SourceUnavailable,
    StorageUnavailable,
)

log = logging.getLogger(__name__)

PROBLEM_BASE = "https://kiakia.app/problems/"
PROBLEM_JSON = "application/problem+json"

# Pretty names for inputs, as clients know them (the contract's names).
_FIELD_NAMES = {"idempotency-key": "Idempotency-Key"}

# Plain-language messages for errors a UI shows under a form field. Other errors keep
# pydantic's message, which is already readable ("Input should be less than or equal to 100").
_FRIENDLY = {
    ("text", "string_pattern_mismatch"): "must not be empty or only spaces",
    ("text", "string_too_short"): "must not be empty",
    ("text", "string_too_long"): "must be at most 4096 characters",
    ("text", "missing"): "is required",
    ("Idempotency-Key", "missing"): "is required (send a unique value per reply, e.g. a UUID)",
    ("Idempotency-Key", "string_pattern_mismatch"): "may only contain letters, digits, '-' and '_'",
}


def problem(
    request: Request,
    status: int,
    slug: str,
    title: str,
    detail: str | None = None,
    errors: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": PROBLEM_BASE + slug,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
    }
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def _field_errors(exc: RequestValidationError) -> tuple[list[dict[str, str]], bool]:
    """Turn FastAPI/pydantic validation errors into [{field, message}], and say whether
    they are all about body fields (-> 422) rather than a malformed request (-> 400)."""
    errors = []
    body_fields_only = True
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ())]
        # ("body", "text") is a body FIELD. ("body",) alone, or bad JSON, means the body itself
        # is malformed; query/path/header problems are malformed requests too.
        is_body_field = len(loc) >= 2 and loc[0] == "body" and err.get("type") != "json_invalid"
        body_fields_only = body_fields_only and is_body_field
        field = loc[-1] if len(loc) >= 2 else (loc[0] if loc else "request")
        name = _FIELD_NAMES.get(field.lower(), field)
        message = _FRIENDLY.get((name, str(err.get("type"))), str(err.get("msg", "is invalid")))
        errors.append({"field": name, "message": message})
    return errors, body_fields_only


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def on_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors, body_fields_only = _field_errors(exc)
        count = "1 field is invalid." if len(errors) == 1 else f"{len(errors)} fields are invalid."
        if body_fields_only:
            return problem(request, 422, "validation-error", "Your request has invalid fields", count, errors)
        return problem(request, 400, "bad-request", "The request is malformed", count, errors)

    @app.exception_handler(InvalidCursor)
    async def on_invalid_cursor(request: Request, exc: InvalidCursor) -> JSONResponse:
        return problem(
            request,
            400,
            "bad-request",
            "The request is malformed",
            "1 parameter is invalid.",
            [{"field": "cursor", "message": str(exc)}],
        )

    @app.exception_handler(InvalidInput)
    async def on_invalid_input(request: Request, exc: InvalidInput) -> JSONResponse:
        return problem(
            request,
            422,
            "validation-error",
            "Your request has invalid fields",
            "1 field is invalid.",
            [{"field": exc.field, "message": exc.message}],
        )

    @app.exception_handler(IdempotencyConflict)
    async def on_idempotency_conflict(request: Request, exc: IdempotencyConflict) -> JSONResponse:
        return problem(request, 409, "idempotency-key-reused", "Idempotency-Key conflict", str(exc))

    @app.exception_handler(DeliveryUnknown)
    async def on_delivery_unknown(request: Request, exc: DeliveryUnknown) -> JSONResponse:
        return problem(
            request,
            502,
            "delivery-unknown",
            "We could not confirm the message was delivered",
            f"{exc} Check the chat before sending again.",
        )

    @app.exception_handler(NotFound)
    async def on_not_found(request: Request, exc: NotFound) -> JSONResponse:
        return problem(request, 404, "not-found", "Not found", str(exc))

    @app.exception_handler(SourceRejectedRequest)
    async def on_channel_rejected(request: Request, exc: SourceRejectedRequest) -> JSONResponse:
        return problem(request, 502, "channel-error", "The channel could not deliver the message", str(exc))

    @app.exception_handler(SourceUnavailable)
    async def on_channel_unavailable(request: Request, exc: SourceUnavailable) -> JSONResponse:
        headers = None
        if isinstance(exc, RateLimited) and exc.retry_after is not None:
            headers = {"Retry-After": str(int(exc.retry_after))}
        return problem(
            request, 503, "service-unavailable", "Service temporarily unavailable", str(exc), headers=headers
        )

    @app.exception_handler(StorageUnavailable)
    async def on_storage_unavailable(request: Request, exc: StorageUnavailable) -> JSONResponse:
        log.error("Storage unavailable: %s", exc)
        return problem(
            request,
            503,
            "service-unavailable",
            "Service temporarily unavailable",
            "The database is not reachable.",
        )

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return problem(request, 404, "not-found", "Not found", "No such endpoint.")
        # Keep headers the HTTP standard requires, e.g. `Allow` on 405 (RFC 9110).
        return problem(
            request,
            exc.status_code,
            "http-error",
            str(exc.detail),
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(Exception)
    async def on_unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unexpected error on %s %s", request.method, request.url.path)
        return problem(request, 500, "internal-error", "Something went wrong on our side")
