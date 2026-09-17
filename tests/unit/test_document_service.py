from datetime import UTC, datetime

import pytest

from app.exceptions import PdfExtractRejectedError
from app.schemas.documents import PdfDocument, ProcessedDocument
from app.schemas.pdf_extract import ExtractedDocument
from app.services.document_service import DocumentService

PDF_CONTENT = b"%PDF-1.4\n%contenido de prueba"
CREATED_AT = datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC)


class StubPdfExtractClient:
    """Doble del client: registra la llamada y devuelve (o lanza) lo configurado."""

    def __init__(self, result: ExtractedDocument | Exception) -> None:
        self._result = result
        self.calls: list[tuple[PdfDocument, str]] = []

    async def create_document(self, document: PdfDocument, name: str) -> ExtractedDocument:
        self.calls.append((document, name))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def make_document(filename: str = "informe.pdf") -> PdfDocument:
    return PdfDocument(filename=filename, content_type="application/pdf", content=PDF_CONTENT)


def make_extracted(name: str = "informe") -> ExtractedDocument:
    return ExtractedDocument(
        id=7,
        name=name,
        original_filename="informe.pdf",
        file_size=len(PDF_CONTENT),
        checksum="a" * 64,
        extracted_text="Texto extraido",
        is_processed=True,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )


async def test_process_pdf_llama_al_client_y_traduce_la_respuesta() -> None:
    client = StubPdfExtractClient(make_extracted())
    service = DocumentService(pdf_extract_client=client)
    document = make_document()

    result = await service.process_pdf(document, name="informe")

    assert client.calls == [(document, "informe")]
    assert result == ProcessedDocument(
        document_id=7,
        name="informe",
        filename="informe.pdf",
        size_bytes=len(PDF_CONTENT),
        checksum="a" * 64,
        is_processed=True,
        extracted_text="Texto extraido",
        processed_at=CREATED_AT,
    )


async def test_process_pdf_usa_el_nombre_del_archivo_si_no_se_indica_nombre() -> None:
    client = StubPdfExtractClient(make_extracted(name="informe final"))
    service = DocumentService(pdf_extract_client=client)

    await service.process_pdf(make_document("informe final.PDF"), name=None)

    assert client.calls[0][1] == "informe final"


async def test_process_pdf_ignora_un_nombre_vacio() -> None:
    client = StubPdfExtractClient(make_extracted())
    service = DocumentService(pdf_extract_client=client)

    await service.process_pdf(make_document(), name="   ")

    assert client.calls[0][1] == "informe"


async def test_process_pdf_propaga_los_errores_del_client() -> None:
    client = StubPdfExtractClient(PdfExtractRejectedError("checksum duplicado"))
    service = DocumentService(pdf_extract_client=client)

    with pytest.raises(PdfExtractRejectedError, match="checksum duplicado"):
        await service.process_pdf(make_document(), name="informe")
