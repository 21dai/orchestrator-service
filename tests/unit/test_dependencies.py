from app.clients.document_store_client import DocumentStoreClient
from app.clients.pdf_extract_client import PdfExtractClient
from app.config import get_settings
from app.dependencies import (
    get_document_service,
    get_document_store_client,
    get_pdf_extract_client,
)
from app.services.document_service import DocumentService


def test_get_document_service_inyecta_los_dos_clients_en_el_service() -> None:
    extractor, store = object(), object()

    service = get_document_service(
        pdf_extract_client=extractor,  # type: ignore[arg-type]
        document_store_client=store,  # type: ignore[arg-type]
    )

    assert isinstance(service, DocumentService)
    assert service._pdf_extract is extractor
    assert service._document_store is store


async def test_get_pdf_extract_client_devuelve_un_singleton_cacheado() -> None:
    first = get_pdf_extract_client()
    second = get_pdf_extract_client()

    assert isinstance(first, PdfExtractClient)
    assert first is second

    await first.aclose()
    get_pdf_extract_client.cache_clear()


async def test_get_pdf_extract_client_aplica_la_politica_de_retry_configurada() -> None:
    client = get_pdf_extract_client()

    assert client._retry_policy is not None
    assert client._retry_policy.max_attempts == get_settings().retry_max_attempts

    await client.aclose()
    get_pdf_extract_client.cache_clear()


async def test_get_pdf_extract_client_tiene_su_propio_circuit_breaker() -> None:
    client = get_pdf_extract_client()

    assert client._circuit_breaker is not None
    assert (
        client._circuit_breaker.failure_threshold
        == get_settings().circuit_breaker_failure_threshold
    )

    await client.aclose()
    get_pdf_extract_client.cache_clear()


async def test_get_pdf_extract_client_tiene_su_propio_bulkhead() -> None:
    client = get_pdf_extract_client()

    assert client._bulkhead is not None
    assert client._bulkhead.max_concurrent == get_settings().bulkhead_max_concurrent

    await client.aclose()
    get_pdf_extract_client.cache_clear()


async def test_get_pdf_extract_client_usa_los_dos_timeouts() -> None:
    client = get_pdf_extract_client()
    settings = get_settings()

    assert client._client.timeout.connect == settings.http_connect_timeout_seconds
    assert client._client.timeout.read == settings.pdf_extract_timeout_seconds

    await client.aclose()
    get_pdf_extract_client.cache_clear()


async def test_get_document_store_client_devuelve_un_singleton_con_su_url() -> None:
    first = get_document_store_client()
    second = get_document_store_client()

    assert isinstance(first, DocumentStoreClient)
    assert first is second
    assert str(first.base_url).rstrip("/") == get_settings().document_store_base_url

    await first.aclose()
    get_document_store_client.cache_clear()


async def test_get_document_store_client_tiene_su_propia_resiliencia() -> None:
    store = get_document_store_client()
    extractor = get_pdf_extract_client()
    settings = get_settings()

    # Un breaker y un bulkhead por microservicio: si document-store se cae,
    # no se corta la extracción, y al revés.
    assert store._circuit_breaker is not None
    assert store._circuit_breaker is not extractor._circuit_breaker
    assert store._bulkhead is not None
    assert store._bulkhead is not extractor._bulkhead
    assert store._retry_policy is not None
    assert store._client.timeout.read == settings.document_store_timeout_seconds
    assert store._client.timeout.connect == settings.http_connect_timeout_seconds

    await store.aclose()
    await extractor.aclose()
    get_document_store_client.cache_clear()
    get_pdf_extract_client.cache_clear()
