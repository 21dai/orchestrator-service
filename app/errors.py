"""Traducción de excepciones a respuestas HTTP (RFC 9457 Problem Details).

Mismo formato que usa pdf-extractext, para que los clientes del equipo vean
errores consistentes en todos los microservicios:

    {"type", "title", "status", "detail", "instance"}

Cubre excepciones de dominio, errores de validación de FastAPI,
errores HTTP de Starlette (404/405) y un fallback para fallos inesperados.
"""

from collections.abc import Awaitable, Callable
from http import HTTPStatus

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.exceptions import (
    InvalidPdfError,
    OrchestratorError,
    PdfExtractCircuitOpenError,
    PdfExtractOverloadedError,
    PdfExtractRejectedError,
    PdfExtractTimeoutError,
    PdfExtractUnavailableError,
    PdfExtractUnexpectedResponseError,
)

PROBLEM_JSON = "application/problem+json"

# Qué código HTTP corresponde a cada excepción de dominio. Los fallos del
# microservicio hoja se reportan con códigos de gateway (502/503/504) para
# distinguirlos de errores propios del orquestador.
STATUS_BY_EXCEPTION: dict[type[OrchestratorError], int] = {
    InvalidPdfError: status.HTTP_400_BAD_REQUEST,
    PdfExtractRejectedError: status.HTTP_400_BAD_REQUEST,
    PdfExtractUnexpectedResponseError: status.HTTP_502_BAD_GATEWAY,
    PdfExtractUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    PdfExtractCircuitOpenError: status.HTTP_503_SERVICE_UNAVAILABLE,
    PdfExtractOverloadedError: status.HTTP_503_SERVICE_UNAVAILABLE,
    PdfExtractTimeoutError: status.HTTP_504_GATEWAY_TIMEOUT,
}


def problem_response(request: Request, status_code: int, detail: str) -> JSONResponse:
    """Arma una respuesta Problem Details para el `status_code` y `detail` dados."""
    payload = {
        "type": "about:blank",
        "title": HTTPStatus(status_code).phrase,
        "status": status_code,
        "detail": detail,
        "instance": str(request.url),
    }
    return JSONResponse(status_code=status_code, content=payload, media_type=PROBLEM_JSON)


def register_error_handlers(app: FastAPI) -> None:
    """Registra handlers globales con formato Problem Details."""

    for exc_type, status_code in STATUS_BY_EXCEPTION.items():
        app.add_exception_handler(exc_type, _make_handler(status_code))

    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected_error)


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Request inválida (p. ej. falta `file`): 422 con qué campo falló."""
    parts = []
    for error in exc.errors():
        loc = ".".join(str(p) for p in error.get("loc", ()))
        parts.append(f"{loc}: {error.get('msg')}" if loc else str(error.get("msg")))
    detail = f"Error de validación: {'; '.join(parts)}" if parts else "Error de validación."
    return problem_response(request, status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """404/405/etc: conserva el status original pero con formato Problem Details."""
    return problem_response(request, exc.status_code, str(exc.detail))


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Fallo no previsto: 500 con detail genérico, sin exponer internals."""
    return problem_response(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Ocurrió un error inesperado.",
    )


ExceptionHandler = Callable[[Request, Exception], Awaitable[JSONResponse]]


def _make_handler(status_code: int) -> ExceptionHandler:
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(request, status_code, str(exc))

    return handler
