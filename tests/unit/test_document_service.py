from app.schemas.documents import PdfDocument, ReceivedDocument
from app.services.document_service import DocumentService

PDF_CONTENT = b"%PDF-1.4\n%contenido de prueba"


async def test_receive_pdf_devuelve_metadatos_del_documento() -> None:
    service = DocumentService()
    document = PdfDocument(
        filename="informe.pdf",
        content_type="application/pdf",
        content=PDF_CONTENT,
    )

    result = await service.receive_pdf(document)

    assert result == ReceivedDocument(
        filename="informe.pdf",
        content_type="application/pdf",
        size_bytes=len(PDF_CONTENT),
    )
