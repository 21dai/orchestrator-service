import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.clients.bulkhead import Bulkhead
from app.clients.circuit_breaker import CircuitBreaker, CircuitState
from app.clients.document_store_client import DocumentStoreClient
from app.exceptions import (
    DocumentAlreadyExistsError,
    DocumentStoreCircuitOpenError,
    DocumentStoreOverloadedError,
    DocumentStoreRejectedError,
    DocumentStoreTimeoutError,
    DocumentStoreUnavailableError,
    DocumentStoreUnexpectedResponseError,
)
from app.schemas.document_store import NewDocument, StoredDocument

BASE_URL = "http://document-store:8000"
DOCUMENTS_URL = f"{BASE_URL}/api/v1/documents"
CHECKSUM = "a" * 64

NEW_DOCUMENT = NewDocument(
    name="informe",
    original_filename="informe.pdf",
    checksum=CHECKSUM,
    file_size=1024,
    page_count=3,
    extracted_text="# Informe\n\nTexto extraido",
    is_processed=True,
)

STORED_RESPONSE = {
    "id": 7,
    "name": "informe",
    "original_filename": "informe.pdf",
    "checksum": CHECKSUM,
    "file_size": 1024,
    "page_count": 3,
    "extracted_text": "# Informe\n\nTexto extraido",
    "is_processed": True,
    "created_at": "2026-10-23T10:00:00Z",
    "updated_at": "2026-10-23T10:00:00Z",
}


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


def make_client(**kwargs: object) -> DocumentStoreClient:
    return DocumentStoreClient(base_url=BASE_URL, timeout_seconds=5, **kwargs)  # type: ignore[arg-type]


@respx.mock
async def test_create_document_envia_el_json_del_contrato_y_mapea_el_201() -> None:
    route = respx.post(DOCUMENTS_URL).mock(return_value=httpx.Response(201, json=STORED_RESPONSE))

    async with make_client() as client:
        result = await client.create_document(NEW_DOCUMENT)

    sent = route.calls.last.request
    assert sent.headers["content-type"] == "application/json"
    assert json.loads(sent.content) == {
        "name": "informe",
        "original_filename": "informe.pdf",
        "checksum": CHECKSUM,
        "file_size": 1024,
        "page_count": 3,
        "extracted_text": "# Informe\n\nTexto extraido",
        "is_processed": True,
    }
    created_at = datetime(2026, 10, 23, 10, 0, 0, tzinfo=UTC)
    assert result == StoredDocument(
        id=7,
        name="informe",
        original_filename="informe.pdf",
        checksum=CHECKSUM,
        file_size=1024,
        page_count=3,
        extracted_text="# Informe\n\nTexto extraido",
        is_processed=True,
        created_at=created_at,
        updated_at=created_at,
    )


@respx.mock
async def test_create_document_traduce_el_409_a_documento_duplicado() -> None:
    respx.post(DOCUMENTS_URL).mock(
        return_value=problem(409, f"Ya existe un documento con el checksum {CHECKSUM}.")
    )

    async with make_client() as client:
        with pytest.raises(DocumentAlreadyExistsError, match="Ya existe") as caught:
            await client.create_document(NEW_DOCUMENT)

    assert caught.value.checksum == CHECKSUM


@pytest.mark.parametrize("status", [400, 404, 422])
@respx.mock
async def test_create_document_traduce_otro_4xx_a_rechazo_con_el_detalle(status: int) -> None:
    # El orquestador ya validó los datos: otro 4xx es un contrato roto o una
    # URL mal configurada, no un error del cliente.
    respx.post(DOCUMENTS_URL).mock(return_value=problem(status, "Datos inválidos: checksum"))

    async with make_client() as client:
        with pytest.raises(DocumentStoreRejectedError, match="Datos inválidos: checksum"):
            await client.create_document(NEW_DOCUMENT)


@respx.mock
async def test_create_document_traduce_5xx_a_respuesta_inesperada() -> None:
    respx.post(DOCUMENTS_URL).mock(return_value=httpx.Response(500, text="boom"))

    async with make_client() as client:
        with pytest.raises(DocumentStoreUnexpectedResponseError, match="500"):
            await client.create_document(NEW_DOCUMENT)


@respx.mock
async def test_create_document_traduce_body_invalido_a_respuesta_inesperada() -> None:
    respx.post(DOCUMENTS_URL).mock(return_value=httpx.Response(201, json={"id": "x"}))

    async with make_client() as client:
        with pytest.raises(DocumentStoreUnexpectedResponseError):
            await client.create_document(NEW_DOCUMENT)


@respx.mock
async def test_create_document_trata_un_503_sin_retry_after_como_no_disponible() -> None:
    # Así responde document-store cuando MongoDB no está disponible.
    respx.post(DOCUMENTS_URL).mock(
        return_value=problem(503, "La base de datos no está disponible; reintentar más tarde.")
    )

    async with make_client() as client:
        with pytest.raises(DocumentStoreUnavailableError, match="base de datos"):
            await client.create_document(NEW_DOCUMENT)


@respx.mock
async def test_create_document_traduce_un_503_con_retry_after_a_sobrecarga() -> None:
    response = problem(503, "Servicio saturado")
    response.headers["Retry-After"] = "4"
    respx.post(DOCUMENTS_URL).mock(return_value=response)

    async with make_client() as client:
        with pytest.raises(DocumentStoreOverloadedError, match="saturado") as caught:
            await client.create_document(NEW_DOCUMENT)

    assert caught.value.retry_after_seconds == 4


@pytest.mark.parametrize(
    ("side_effect", "expected"),
    [
        (httpx.ConnectError("refused"), DocumentStoreUnavailableError),
        (httpx.ConnectTimeout("timeout"), DocumentStoreUnavailableError),
        (httpx.ReadTimeout("timeout"), DocumentStoreTimeoutError),
        (httpx.RemoteProtocolError("cortado"), DocumentStoreUnavailableError),
    ],
)
@respx.mock
async def test_create_document_traduce_los_fallos_de_red(
    side_effect: Exception, expected: type[Exception]
) -> None:
    respx.post(DOCUMENTS_URL).mock(side_effect=side_effect)

    async with make_client() as client:
        with pytest.raises(expected, match="document-store"):
            await client.create_document(NEW_DOCUMENT)


@respx.mock
async def test_create_document_traduce_el_circuito_abierto_con_retry_after() -> None:
    respx.post(DOCUMENTS_URL).mock(side_effect=httpx.ConnectError("refused"))
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=30)

    async with make_client(circuit_breaker=breaker) as client:
        with pytest.raises(DocumentStoreUnavailableError):
            await client.create_document(NEW_DOCUMENT)
        with pytest.raises(DocumentStoreCircuitOpenError, match="temporalmente") as caught:
            await client.create_document(NEW_DOCUMENT)

    assert caught.value.retry_after_seconds == pytest.approx(30, abs=1)


async def test_create_document_traduce_el_bulkhead_lleno() -> None:
    bulkhead = Bulkhead(max_concurrent=1, max_waiting=0, acquire_timeout_seconds=1)
    async with bulkhead.slot():  # alguien más ya ocupa el único slot
        async with make_client(bulkhead=bulkhead) as client:
            with pytest.raises(DocumentStoreOverloadedError, match="en curso"):
                await client.create_document(NEW_DOCUMENT)


@respx.mock
async def test_un_409_no_cuenta_como_fallo_para_el_circuit_breaker() -> None:
    respx.post(DOCUMENTS_URL).mock(return_value=problem(409, "duplicado"))
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=30)

    async with make_client(circuit_breaker=breaker) as client:
        for _ in range(3):
            with pytest.raises(DocumentAlreadyExistsError):
                await client.create_document(NEW_DOCUMENT)

    assert breaker.state is CircuitState.CLOSED
