import hashlib
from datetime import UTC, datetime

import pytest

from app.exceptions import (
    DocumentAlreadyExistsError,
    DocumentStoreUnavailableError,
    InvalidDocumentNameError,
    PdfExtractRejectedError,
)
from app.schemas.document_store import NewDocument, StoredDocument
from app.schemas.documents import PdfDocument, ProcessedDocument
from app.schemas.pdf_extract import ExtractionResult
from app.services.document_service import DocumentService

PDF_CONTENT = b"%PDF-1.4\n%contenido de prueba"
CHECKSUM = hashlib.sha256(PDF_CONTENT).hexdigest()
CREATED_AT = datetime(2026, 10, 23, 10, 0, 0, tzinfo=UTC)
EXTRACTION = ExtractionResult(content="# Informe\n\nTexto extraido", page_count=3)


class StubPdfExtractClient:
    """Doble del client de extracción: registra la llamada y devuelve (o lanza)."""

    def __init__(self, result: ExtractionResult | Exception = EXTRACTION) -> None:
        self._result = result
        self.calls: list[PdfDocument] = []

    async def extract(self, document: PdfDocument) -> ExtractionResult:
        self.calls.append(document)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class StubDocumentStoreClient:
    """Doble de document-store: guarda lo que recibe y le asigna el id 7."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[NewDocument] = []

    async def create_document(self, document: NewDocument) -> StoredDocument:
        self.calls.append(document)
        if self._error is not None:
            raise self._error
        return StoredDocument(
            **document.model_dump(), id=7, created_at=CREATED_AT, updated_at=CREATED_AT
        )


def make_document(filename: str = "informe.pdf") -> PdfDocument:
    return PdfDocument(filename=filename, content_type="application/pdf", content=PDF_CONTENT)


def make_service(
    extractor: StubPdfExtractClient | None = None, store: StubDocumentStoreClient | None = None
) -> tuple[DocumentService, StubPdfExtractClient, StubDocumentStoreClient]:
    extractor = extractor or StubPdfExtractClient()
    store = store or StubDocumentStoreClient()
    service = DocumentService(pdf_extract_client=extractor, document_store_client=store)
    return service, extractor, store


async def test_process_pdf_extrae_el_texto_y_guarda_el_documento_en_document_store() -> None:
    service, extractor, store = make_service()
    document = make_document()

    result = await service.process_pdf(document, name="informe")

    assert extractor.calls == [document]
    assert store.calls == [
        NewDocument(
            name="informe",
            original_filename="informe.pdf",
            checksum=CHECKSUM,
            file_size=len(PDF_CONTENT),
            page_count=3,
            extracted_text="# Informe\n\nTexto extraido",
            is_processed=True,
        )
    ]
    assert result == ProcessedDocument(
        document_id=7,
        name="informe",
        filename="informe.pdf",
        size_bytes=len(PDF_CONTENT),
        checksum=CHECKSUM,
        page_count=3,
        is_processed=True,
        extracted_text="# Informe\n\nTexto extraido",
        processed_at=CREATED_AT,
    )


async def test_process_pdf_envia_el_sha256_de_los_bytes_del_pdf() -> None:
    service, _, store = make_service()

    await service.process_pdf(make_document(), name="informe")

    assert store.calls[0].checksum == hashlib.sha256(PDF_CONTENT).hexdigest()


async def test_process_pdf_usa_el_nombre_del_archivo_si_no_se_indica_nombre() -> None:
    service, _, store = make_service()

    await service.process_pdf(make_document("informe final.PDF"), name=None)

    assert store.calls[0].name == "informe final"
    assert store.calls[0].original_filename == "informe final.PDF"


async def test_process_pdf_ignora_un_nombre_vacio() -> None:
    service, _, store = make_service()

    await service.process_pdf(make_document(), name="   ")

    assert store.calls[0].name == "informe"


async def test_process_pdf_normaliza_el_nombre_quitando_espacios() -> None:
    service, _, store = make_service()

    await service.process_pdf(make_document(), name="  mi informe  ")

    assert store.calls[0].name == "mi informe"


async def test_process_pdf_acepta_un_nombre_de_255_caracteres() -> None:
    service, _, store = make_service()

    await service.process_pdf(make_document(), name="a" * 255)

    assert store.calls[0].name == "a" * 255


async def test_process_pdf_rechaza_un_nombre_largo_antes_de_extraer() -> None:
    service, extractor, store = make_service()

    with pytest.raises(InvalidDocumentNameError, match="255"):
        await service.process_pdf(make_document(), name="a" * 256)

    assert extractor.calls == []
    assert store.calls == []


async def test_si_falla_la_extraccion_no_se_guarda_nada() -> None:
    service, _, store = make_service(
        extractor=StubPdfExtractClient(PdfExtractRejectedError("El PDF está dañado"))
    )

    with pytest.raises(PdfExtractRejectedError, match="dañado"):
        await service.process_pdf(make_document(), name="informe")

    assert store.calls == []


@pytest.mark.parametrize(
    "error",
    [
        DocumentAlreadyExistsError("Ya existe un documento", checksum=CHECKSUM),
        DocumentStoreUnavailableError("document-store no está disponible"),
    ],
)
async def test_process_pdf_propaga_los_errores_de_document_store(error: Exception) -> None:
    service, _, _ = make_service(store=StubDocumentStoreClient(error))

    with pytest.raises(type(error)):
        await service.process_pdf(make_document(), name="informe")
