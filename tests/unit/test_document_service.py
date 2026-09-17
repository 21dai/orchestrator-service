import pytest

from app.exceptions import InvalidPdfError
from app.schemas.documents import ReceivedDocument
from app.services.document_service import DocumentService

PDF_CONTENT = b"%PDF-1.4\n%contenido de prueba"


async def test_receive_pdf_devuelve_metadatos_del_archivo() -> None:
    service = DocumentService()

    result = await service.receive_pdf(
        filename="informe.pdf",
        content_type="application/pdf",
        content=PDF_CONTENT,
    )

    assert result == ReceivedDocument(
        filename="informe.pdf",
        content_type="application/pdf",
        size_bytes=len(PDF_CONTENT),
    )


async def test_receive_pdf_rechaza_archivo_invalido() -> None:
    service = DocumentService()

    with pytest.raises(InvalidPdfError):
        await service.receive_pdf(
            filename="notas.txt",
            content_type="text/plain",
            content=b"texto plano",
        )
