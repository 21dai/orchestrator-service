from app.clients.pdf_extract_client import PdfExtractClient
from app.config import get_settings
from app.dependencies import get_document_service, get_pdf_extract_client
from app.services.document_service import DocumentService


def test_get_document_service_inyecta_el_client_en_el_service() -> None:
    gateway = object()

    service = get_document_service(pdf_extract_client=gateway)  # type: ignore[arg-type]

    assert isinstance(service, DocumentService)
    assert service._pdf_extract is gateway


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
