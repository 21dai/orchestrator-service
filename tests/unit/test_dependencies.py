from app.clients.pdf_extract_client import PdfExtractClient
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
