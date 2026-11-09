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
    DocumentNotFoundError,
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


# --- CRUD: GET, PUT y DELETE de un documento, y la lista -----------------------

DOCUMENT_URL = f"{DOCUMENTS_URL}/7"

STORED_DOCUMENT = StoredDocument.model_validate(STORED_RESPONSE)

#: Fallos de red de cualquier operación y la excepción de dominio que corresponde.
NETWORK_FAILURES = [
    (httpx.ConnectError("refused"), DocumentStoreUnavailableError),
    (httpx.ReadTimeout("timeout"), DocumentStoreTimeoutError),
]


@respx.mock
async def test_get_document_pide_el_documento_por_id_y_mapea_el_200() -> None:
    route = respx.get(DOCUMENT_URL).mock(return_value=httpx.Response(200, json=STORED_RESPONSE))

    async with make_client() as client:
        result = await client.get_document(7)

    assert route.called
    assert result == STORED_DOCUMENT


@respx.mock
async def test_get_document_traduce_el_404_a_documento_inexistente() -> None:
    respx.get(DOCUMENT_URL).mock(return_value=problem(404, "No existe un documento con id 7."))

    async with make_client() as client:
        with pytest.raises(DocumentNotFoundError, match="id 7") as caught:
            await client.get_document(7)

    assert caught.value.document_id == 7


@respx.mock
async def test_get_document_traduce_5xx_a_respuesta_inesperada() -> None:
    respx.get(DOCUMENT_URL).mock(return_value=httpx.Response(500, text="boom"))

    async with make_client() as client:
        with pytest.raises(DocumentStoreUnexpectedResponseError, match="500"):
            await client.get_document(7)


@pytest.mark.parametrize(("side_effect", "expected"), NETWORK_FAILURES)
@respx.mock
async def test_get_document_traduce_los_fallos_de_red(
    side_effect: Exception, expected: type[Exception]
) -> None:
    respx.get(DOCUMENT_URL).mock(side_effect=side_effect)

    async with make_client() as client:
        with pytest.raises(expected, match="document-store"):
            await client.get_document(7)


@respx.mock
async def test_list_documents_pasa_skip_y_limit_y_mapea_la_lista() -> None:
    route = respx.get(DOCUMENTS_URL).mock(
        return_value=httpx.Response(200, json=[STORED_RESPONSE, {**STORED_RESPONSE, "id": 8}])
    )

    async with make_client() as client:
        result = await client.list_documents(skip=20, limit=5)

    assert dict(route.calls.last.request.url.params) == {"skip": "20", "limit": "5"}
    assert [document.id for document in result] == [7, 8]
    assert result[0] == STORED_DOCUMENT


@respx.mock
async def test_list_documents_devuelve_una_lista_vacia_si_no_hay_documentos() -> None:
    respx.get(DOCUMENTS_URL).mock(return_value=httpx.Response(200, json=[]))

    async with make_client() as client:
        assert await client.list_documents(skip=0, limit=10) == []


@pytest.mark.parametrize("body", [{"id": 7}, [{"id": "x"}]])
@respx.mock
async def test_list_documents_traduce_body_invalido_a_respuesta_inesperada(body: object) -> None:
    respx.get(DOCUMENTS_URL).mock(return_value=httpx.Response(200, json=body))

    async with make_client() as client:
        with pytest.raises(DocumentStoreUnexpectedResponseError, match="formato"):
            await client.list_documents(skip=0, limit=10)


@respx.mock
async def test_list_documents_traduce_5xx_a_respuesta_inesperada() -> None:
    respx.get(DOCUMENTS_URL).mock(return_value=httpx.Response(500, text="boom"))

    async with make_client() as client:
        with pytest.raises(DocumentStoreUnexpectedResponseError, match="500"):
            await client.list_documents(skip=0, limit=10)


@respx.mock
async def test_list_documents_traduce_un_4xx_a_rechazo() -> None:
    # El orquestador ya validó skip y limit: un 400 es un contrato roto (502).
    respx.get(DOCUMENTS_URL).mock(return_value=problem(400, "limit fuera de rango"))

    async with make_client() as client:
        with pytest.raises(DocumentStoreRejectedError, match="limit fuera de rango"):
            await client.list_documents(skip=0, limit=10)


@pytest.mark.parametrize(("side_effect", "expected"), NETWORK_FAILURES)
@respx.mock
async def test_list_documents_traduce_los_fallos_de_red(
    side_effect: Exception, expected: type[Exception]
) -> None:
    respx.get(DOCUMENTS_URL).mock(side_effect=side_effect)

    async with make_client() as client:
        with pytest.raises(expected, match="document-store"):
            await client.list_documents(skip=0, limit=10)


@respx.mock
async def test_update_document_envia_solo_el_nombre_y_mapea_el_200() -> None:
    renamed = {**STORED_RESPONSE, "name": "nuevo nombre", "updated_at": "2026-11-09T15:30:00Z"}
    route = respx.put(DOCUMENT_URL).mock(return_value=httpx.Response(200, json=renamed))

    async with make_client() as client:
        result = await client.update_document(7, name="nuevo nombre")

    # document-store rechaza cualquier campo además de name (extra="forbid").
    assert json.loads(route.calls.last.request.content) == {"name": "nuevo nombre"}
    assert result.name == "nuevo nombre"
    assert result.updated_at == datetime(2026, 11, 9, 15, 30, 0, tzinfo=UTC)


@respx.mock
async def test_update_document_traduce_el_404_a_documento_inexistente() -> None:
    respx.put(DOCUMENT_URL).mock(return_value=problem(404, "No existe un documento con id 7."))

    async with make_client() as client:
        with pytest.raises(DocumentNotFoundError, match="id 7"):
            await client.update_document(7, name="nuevo nombre")


@respx.mock
async def test_update_document_traduce_un_400_a_rechazo() -> None:
    # El orquestador ya validó el nombre: un 400 es un contrato roto (502).
    respx.put(DOCUMENT_URL).mock(return_value=problem(400, "name: demasiado largo"))

    async with make_client() as client:
        with pytest.raises(DocumentStoreRejectedError, match="demasiado largo"):
            await client.update_document(7, name="nuevo nombre")


@respx.mock
async def test_update_document_traduce_5xx_a_respuesta_inesperada() -> None:
    respx.put(DOCUMENT_URL).mock(return_value=httpx.Response(500, text="boom"))

    async with make_client() as client:
        with pytest.raises(DocumentStoreUnexpectedResponseError, match="500"):
            await client.update_document(7, name="nuevo nombre")


@pytest.mark.parametrize(("side_effect", "expected"), NETWORK_FAILURES)
@respx.mock
async def test_update_document_traduce_los_fallos_de_red(
    side_effect: Exception, expected: type[Exception]
) -> None:
    respx.put(DOCUMENT_URL).mock(side_effect=side_effect)

    async with make_client() as client:
        with pytest.raises(expected, match="document-store"):
            await client.update_document(7, name="nuevo nombre")
