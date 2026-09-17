"""Traducción de excepciones de dominio a respuestas HTTP (RFC 9457 Problem Details).

Mismo formato que usa pdf-extractext, para que los clientes del equipo vean
errores consistentes en todos los microservicios:

    {"type", "title", "status", "detail", "instance"}
"""

from http import HTTPStatus

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.exceptions import InvalidPdfError

PROBLEM_JSON = "application/problem+json"


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

    @app.exception_handler(InvalidPdfError)
    async def invalid_pdf_handler(request: Request, exc: InvalidPdfError) -> JSONResponse:
        return problem_response(request, status.HTTP_400_BAD_REQUEST, str(exc))
