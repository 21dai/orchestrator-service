"""Verifica la traducción de excepciones de dominio a respuestas HTTP.

Usa una app mínima con un endpoint que lanza cada excepción, para probar los
handlers sin depender del flujo de negocio.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import register_error_handlers
from app.exceptions import (
    OrchestratorError,
    PdfExtractRejectedError,
    PdfExtractTimeoutError,
    PdfExtractUnavailableError,
    PdfExtractUnexpectedResponseError,
)


def make_client(exc: OrchestratorError) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise exc

    return TestClient(app)


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (PdfExtractRejectedError("checksum duplicado"), 400),
        (PdfExtractUnexpectedResponseError("respuesta rara"), 502),
        (PdfExtractUnavailableError("no conecta"), 503),
        (PdfExtractTimeoutError("tardó demasiado"), 504),
    ],
)
def test_excepciones_de_pdf_extract_se_traducen_a_problem_details(
    exc: OrchestratorError, expected_status: int
) -> None:
    response = make_client(exc).get("/boom")

    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == expected_status
    assert body["detail"] == str(exc)
    assert body["instance"].endswith("/boom")
