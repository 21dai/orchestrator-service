"""Client de pdf-extractext: `POST /extract`, la extracción sin estado."""

import httpx
import pytest
import respx

from app.clients.bulkhead import Bulkhead
from app.clients.circuit_breaker import CircuitBreaker
from app.clients.pdf_extract_client import PdfExtractClient
from app.exceptions import (
    PdfExtractCircuitOpenError,
    PdfExtractOverloadedError,
    PdfExtractRejectedError,
    PdfExtractTimeoutError,
    PdfExtractUnavailableError,
    PdfExtractUnexpectedResponseError,
)
from app.schemas.documents import PdfDocument
from app.schemas.pdf_extract import ExtractionResult

BASE_URL = "http://pdf-extractext:8000"
EXTRACT_URL = f"{BASE_URL}/extract"
PDF_CONTENT = b"%PDF-1.4\n%contenido de prueba"
EXTRACT_RESPONSE = {"content": "# Informe\n\nTexto extraido", "page_count": 3}


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
            "instance": EXTRACT_URL,
        },
        headers={"content-type": "application/problem+json"},
    )


@respx.mock
async def test_extract_envia_solo_el_archivo_a_extract_y_devuelve_texto_y_paginas() -> None:
    route = respx.post(EXTRACT_URL).mock(return_value=httpx.Response(200, json=EXTRACT_RESPONSE))

    async with PdfExtractClient(base_url=BASE_URL, timeout_seconds=5) as client:
        result = await client.extract(make_document())

    sent = route.calls.last.request
    assert sent.headers["content-type"].startswith("multipart/form-data")
    assert b'filename="informe.pdf"' in sent.content
    assert b"Content-Type: application/pdf" in sent.content
    assert PDF_CONTENT in sent.content
    # /extract no recibe nombre: el documento se registra en document-store.
    assert b'name="name"' not in sent.content
    assert result == ExtractionResult(content="# Informe\n\nTexto extraido", page_count=3)


@pytest.mark.parametrize("status", [400, 413, 422])
@respx.mock
async def test_extract_traduce_un_4xx_a_rechazo_con_el_detalle(status: int) -> None:
    respx.post(EXTRACT_URL).mock(return_value=problem(status, "El PDF está dañado"))

    async with PdfExtractClient(base_url=BASE_URL, timeout_seconds=5) as client:
        with pytest.raises(PdfExtractRejectedError, match="dañado"):
            await client.extract(make_document())


@respx.mock
async def test_extract_usa_el_body_crudo_si_el_detalle_no_es_texto() -> None:
    # Los 422 de FastAPI traen `detail` como lista, no como string.
    respx.post(EXTRACT_URL).mock(
        return_value=httpx.Response(
            422, json={"detail": [{"loc": ["body", "file"], "msg": "campo requerido"}]}
        )
    )

    async with PdfExtractClient(base_url=BASE_URL, timeout_seconds=5) as client:
        with pytest.raises(PdfExtractRejectedError, match="campo requerido"):
            await client.extract(make_document())


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(200, json={"content": "texto"}),
        httpx.Response(200, json={"content": "texto", "page_count": -1}),
    ],
    ids=["5xx", "sin-page_count", "page_count-negativo"],
)
@respx.mock
async def test_extract_traduce_una_respuesta_inesperada(response: httpx.Response) -> None:
    respx.post(EXTRACT_URL).mock(return_value=response)

    async with PdfExtractClient(base_url=BASE_URL, timeout_seconds=5) as client:
        with pytest.raises(PdfExtractUnexpectedResponseError):
            await client.extract(make_document())


@respx.mock
async def test_extract_traduce_la_cola_llena_a_sobrecarga_con_retry_after() -> None:
    response = problem(503, "Cola de extraccion llena")
    response.headers["Retry-After"] = "2"
    respx.post(EXTRACT_URL).mock(return_value=response)

    async with PdfExtractClient(base_url=BASE_URL, timeout_seconds=5) as client:
        with pytest.raises(PdfExtractOverloadedError, match="llena") as caught:
            await client.extract(make_document())

    assert caught.value.retry_after_seconds == 2


@pytest.mark.parametrize(
    ("mock", "expected"),
    [
        # Así responde Traefik cuando no queda ninguna réplica sana.
        (
            {"return_value": httpx.Response(503, text="no available server")},
            PdfExtractUnavailableError,
        ),
        ({"side_effect": httpx.ConnectError("refused")}, PdfExtractUnavailableError),
        # Timeout de conexión: el hoja no está escuchando, no está lento.
        ({"side_effect": httpx.ConnectTimeout("timeout")}, PdfExtractUnavailableError),
        ({"side_effect": httpx.ReadTimeout("timeout")}, PdfExtractTimeoutError),
    ],
    ids=["503-sin-retry-after", "sin-conexion", "timeout-de-conexion", "timeout"],
)
@respx.mock
async def test_extract_traduce_las_caidas(
    mock: dict[str, object], expected: type[Exception]
) -> None:
    respx.post(EXTRACT_URL).mock(**mock)  # type: ignore[arg-type]

    async with PdfExtractClient(base_url=BASE_URL, timeout_seconds=5) as client:
        with pytest.raises(expected, match="pdf-extractext"):
            await client.extract(make_document())


@respx.mock
async def test_extract_traduce_el_circuito_abierto_e_informa_cuando_reintentar() -> None:
    respx.post(EXTRACT_URL).mock(side_effect=httpx.ConnectError("refused"))
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=30)

    async with PdfExtractClient(
        base_url=BASE_URL, timeout_seconds=5, circuit_breaker=breaker
    ) as client:
        with pytest.raises(PdfExtractUnavailableError):
            await client.extract(make_document())
        with pytest.raises(PdfExtractCircuitOpenError, match="temporalmente") as caught:
            await client.extract(make_document())

    assert caught.value.retry_after_seconds == pytest.approx(30, abs=1)


async def test_extract_traduce_el_bulkhead_lleno() -> None:
    bulkhead = Bulkhead(max_concurrent=1, max_waiting=0, acquire_timeout_seconds=1)
    async with bulkhead.slot():  # alguien más ya ocupa el único slot
        async with PdfExtractClient(
            base_url=BASE_URL, timeout_seconds=5, bulkhead=bulkhead
        ) as client:
            with pytest.raises(PdfExtractOverloadedError, match="en curso"):
                await client.extract(make_document())
