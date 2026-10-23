"""Verifica la traducción de excepciones de dominio a respuestas HTTP.

Usa una app mínima con un endpoint que lanza cada excepción, para probar los
handlers sin depender del flujo de negocio.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import register_error_handlers
from app.exceptions import (
    DocumentAlreadyExistsError,
    DocumentStoreCircuitOpenError,
    DocumentStoreOverloadedError,
    DocumentStoreRejectedError,
    DocumentStoreTimeoutError,
    DocumentStoreUnavailableError,
    DocumentStoreUnexpectedResponseError,
    InvalidPdfError,
    OrchestratorError,
    PdfExtractCircuitOpenError,
    PdfExtractOverloadedError,
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
        (InvalidPdfError("no es un pdf"), 400),
        (PdfExtractRejectedError("checksum duplicado"), 400),
        (PdfExtractUnexpectedResponseError("respuesta rara"), 502),
        (PdfExtractUnavailableError("no conecta"), 503),
        (PdfExtractCircuitOpenError("circuito abierto"), 503),
        (PdfExtractOverloadedError("bulkhead lleno"), 503),
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


def test_validacion_de_request_se_traduce_a_422_problem_details() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/items")
    async def get_item(q: str) -> dict[str, str]:
        return {"q": q}

    response = TestClient(app).get("/items")

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Unprocessable Content"
    assert body["status"] == 422
    assert "q" in body["detail"]
    assert body["instance"].endswith("/items")


def test_http_exception_404_se_traduce_a_problem_details() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/existe")
    async def existe() -> dict[str, str]:
        return {"status": "ok"}

    response = TestClient(app).get("/no-existe")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 404
    assert body["detail"]
    assert body["instance"].endswith("/no-existe")


def test_http_exception_405_se_traduce_a_problem_details() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/solo-get")
    async def solo_get() -> dict[str, str]:
        return {"status": "ok"}

    response = TestClient(app).post("/solo-get")

    assert response.status_code == 405
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["status"] == 405


def test_excepcion_inesperada_se_traduce_a_500_generico() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/falla")
    async def falla() -> None:
        raise RuntimeError("secreto interno que no debe exponerse")

    response = TestClient(app, raise_server_exceptions=False).get("/falla")

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Internal Server Error"
    assert body["status"] == 500
    assert body["detail"]
    assert "secreto interno" not in body["detail"]
    assert "secreto interno" not in str(body)
    assert body["instance"].endswith("/falla")


@pytest.mark.parametrize(("retry_after", "header"), [(2, "2"), (1.2, "2"), (0.0, "1")])
def test_las_excepciones_con_retry_after_agregan_el_header(retry_after: float, header: str) -> None:
    exc = PdfExtractOverloadedError("saturado", retry_after_seconds=retry_after)

    response = make_client(exc).get("/boom")

    assert response.status_code == 503
    assert response.headers["retry-after"] == header


def test_sin_retry_after_no_se_agrega_el_header() -> None:
    response = make_client(PdfExtractUnavailableError("no conecta")).get("/boom")

    assert response.status_code == 503
    assert "retry-after" not in response.headers


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (DocumentAlreadyExistsError("Ya existe un documento", checksum="a" * 64), 409),
        # Otro 4xx de document-store es un contrato roto: falla del gateway.
        (DocumentStoreRejectedError("datos inválidos"), 502),
        (DocumentStoreUnexpectedResponseError("respuesta rara"), 502),
        (DocumentStoreUnavailableError("no conecta"), 503),
        (DocumentStoreCircuitOpenError("circuito abierto"), 503),
        (DocumentStoreOverloadedError("bulkhead lleno"), 503),
        (DocumentStoreTimeoutError("tardó demasiado"), 504),
    ],
)
def test_excepciones_de_document_store_se_traducen_a_problem_details(
    exc: OrchestratorError, expected_status: int
) -> None:
    response = make_client(exc).get("/boom")

    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == expected_status
    assert body["detail"] == str(exc)
