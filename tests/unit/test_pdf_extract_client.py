from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.clients.circuit_breaker import CircuitBreaker
from app.clients.pdf_extract_client import PdfExtractClient
from app.exceptions import (
    PdfExtractCircuitOpenError,
    PdfExtractRejectedError,
    PdfExtractTimeoutError,
    PdfExtractUnavailableError,
    PdfExtractUnexpectedResponseError,
)
from app.schemas.documents import PdfDocument
from app.schemas.pdf_extract import ExtractedDocument

BASE_URL = "http://pdf-extractext:8000"
DOCUMENTS_URL = f"{BASE_URL}/api/v1/documents"
PDF_CONTENT = b"%PDF-1.4\n%contenido de prueba"

DOCUMENT_RESPONSE = {
    "id": 7,
    "name": "informe",
    "original_filename": "informe.pdf",
    "file_size": len(PDF_CONTENT),
    "checksum": "a" * 64,
    "extracted_text": "Texto extraido del PDF",
    "is_processed": True,
    "created_at": "2026-09-17T10:00:00Z",
    "updated_at": "2026-09-17T10:00:01Z",
}


def make_document() -> PdfDocument:
    return PdfDocument(filename="informe.pdf", content_type="application/pdf", content=PDF_CONTENT)


def problem(status: int, detail: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "type": "about:blank",
            "title": "Error",
            "status": status,
            "detail": detail,
            "instance": DOCUMENTS_URL,
        },
        headers={"content-type": "application/problem+json"},
    )


@respx.mock
async def test_create_document_envia_multipart_y_mapea_la_respuesta() -> None:
    route = respx.post(DOCUMENTS_URL).mock(return_value=httpx.Response(201, json=DOCUMENT_RESPONSE))

    async with PdfExtractClient(base_url=BASE_URL, timeout_seconds=5) as client:
        result = await client.create_document(make_document(), name="informe")

    sent = route.calls.last.request
    assert sent.headers["content-type"].startswith("multipart/form-data")
    assert b'name="name"\r\n\r\ninforme' in sent.content
    assert b'filename="informe.pdf"' in sent.content
    assert b"Content-Type: application/pdf" in sent.content
    assert PDF_CONTENT in sent.content

    assert result == ExtractedDocument(
        id=7,
        name="informe",
        original_filename="informe.pdf",
        file_size=len(PDF_CONTENT),
        checksum="a" * 64,
        extracted_text="Texto extraido del PDF",
        is_processed=True,
        created_at=datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 9, 17, 10, 0, 1, tzinfo=UTC),
    )


@respx.mock
async def test_create_document_acepta_extracted_text_nulo() -> None:
    respx.post(DOCUMENTS_URL).mock(
        return_value=httpx.Response(201, json={**DOCUMENT_RESPONSE, "extracted_text": None})
    )

    async with PdfExtractClient(base_url=BASE_URL, timeout_seconds=5) as client:
        result = await client.create_document(make_document(), name="informe")

    assert result.extracted_text is None


@respx.mock
async def test_create_document_traduce_400_a_rechazo_con_el_detalle() -> None:
    respx.post(DOCUMENTS_URL).mock(
        return_value=problem(400, "Ya existe un documento con el mismo checksum")
    )

    async with PdfExtractClient(base_url=BASE_URL, timeout_seconds=5) as client:
        with pytest.raises(PdfExtractRejectedError, match="mismo checksum"):
            await client.create_document(make_document(), name="informe")


@respx.mock
async def test_create_document_usa_el_body_crudo_si_el_detalle_no_es_texto() -> None:
    # Los 422 de FastAPI traen `detail` como lista, no como string.
    respx.post(DOCUMENTS_URL).mock(
        return_value=httpx.Response(
            422, json={"detail": [{"loc": ["body", "file"], "msg": "campo requerido"}]}
        )
    )

    async with PdfExtractClient(base_url=BASE_URL, timeout_seconds=5) as client:
        with pytest.raises(PdfExtractRejectedError, match="campo requerido"):
            await client.create_document(make_document(), name="informe")


@respx.mock
async def test_create_document_traduce_5xx_a_respuesta_inesperada() -> None:
    respx.post(DOCUMENTS_URL).mock(return_value=httpx.Response(500, text="boom"))

    async with PdfExtractClient(base_url=BASE_URL, timeout_seconds=5) as client:
        with pytest.raises(PdfExtractUnexpectedResponseError, match="500"):
            await client.create_document(make_document(), name="informe")


@respx.mock
async def test_create_document_traduce_body_invalido_a_respuesta_inesperada() -> None:
    respx.post(DOCUMENTS_URL).mock(return_value=httpx.Response(201, json={"id": "x"}))

    async with PdfExtractClient(base_url=BASE_URL, timeout_seconds=5) as client:
        with pytest.raises(PdfExtractUnexpectedResponseError):
            await client.create_document(make_document(), name="informe")


@respx.mock
async def test_create_document_traduce_timeout() -> None:
    respx.post(DOCUMENTS_URL).mock(side_effect=httpx.ReadTimeout)

    async with PdfExtractClient(base_url=BASE_URL, timeout_seconds=5) as client:
        with pytest.raises(PdfExtractTimeoutError):
            await client.create_document(make_document(), name="informe")


@respx.mock
async def test_create_document_traduce_servicio_no_disponible() -> None:
    respx.post(DOCUMENTS_URL).mock(side_effect=httpx.ConnectError)

    async with PdfExtractClient(base_url=BASE_URL, timeout_seconds=5) as client:
        with pytest.raises(PdfExtractUnavailableError):
            await client.create_document(make_document(), name="informe")


@respx.mock
async def test_create_document_traduce_el_circuito_abierto() -> None:
    respx.post(DOCUMENTS_URL).mock(side_effect=httpx.ConnectError("refused"))
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=30)

    async with PdfExtractClient(
        base_url=BASE_URL, timeout_seconds=5, circuit_breaker=breaker
    ) as client:
        with pytest.raises(PdfExtractUnavailableError):
            await client.create_document(make_document(), name="informe")

        with pytest.raises(PdfExtractCircuitOpenError, match="temporalmente"):
            await client.create_document(make_document(), name="informe")
