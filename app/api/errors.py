import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import DomainError

logger = logging.getLogger("app.errors")


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details or {}}}
    return JSONResponse(jsonable_encoder(body), status_code=status_code, headers=headers)


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{"loc": err["loc"], "msg": err["msg"], "type": err["type"]} for err in exc.errors()]
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "VALIDATION_ERROR",
        "Request validation failed",
        {"errors": errors},
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(
        exc.status_code,
        "HTTP_ERROR",
        str(exc.detail),
        headers=getattr(exc, "headers", None),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Same envelope as other errors; internals go to logs only.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "Internal server error"
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)
