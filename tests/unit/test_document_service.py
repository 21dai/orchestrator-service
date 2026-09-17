from app.schemas.documents import ReceivedDocument
from app.services.document_service import DocumentService


async def test_receive_pdf_devuelve_metadatos_del_archivo() -> None:
    service = DocumentService()
    content = b"%PDF-1.4 contenido de prueba"

    result = await service.receive_pdf(
        filename="informe.pdf",
        content_type="application/pdf",
        content=content,
    )

    assert result == ReceivedDocument(
        filename="informe.pdf",
        content_type="application/pdf",
        size_bytes=len(content),
    )


async def test_receive_pdf_acepta_content_type_ausente() -> None:
    service = DocumentService()

    result = await service.receive_pdf(
        filename="sin-tipo.pdf",
        content_type=None,
        content=b"",
    )

    assert result.content_type is None
    assert result.size_bytes == 0
