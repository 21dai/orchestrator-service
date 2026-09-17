"""Traducción de excepciones de dominio a respuestas HTTP (RFC 9457 Problem Details).

Mismo formato que usa pdf-extractext, para que los clientes del equipo vean
errores consistentes en todos los microservicios:

    {"type", "title", "status", "detail", "instance"}
"""

from collections.abc import Awaitable, Callable
from http import HTTPStatus

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.exceptions import (
    InvalidPdfError,
    OrchestratorError,
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
    """Registra en la app un handler por cada excepción de dominio."""

    for exc_type, status_code in STATUS_BY_EXCEPTION.items():
        app.add_exception_handler(exc_type, _make_handler(status_code))


ExceptionHandler = Callable[[Request, Exception], Awaitable[JSONResponse]]


def _make_handler(status_code: int) -> ExceptionHandler:
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(request, status_code, str(exc))

    return handler
