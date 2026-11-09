"""ProcessedDocument: la respuesta pública traduce el documento de document-store."""

from datetime import UTC, datetime

from app.schemas.document_store import StoredDocument
from app.schemas.documents import ProcessedDocument

CREATED_AT = datetime(2026, 10, 23, 10, 0, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 11, 9, 15, 30, 0, tzinfo=UTC)


def test_from_stored_traduce_los_nombres_del_contrato_de_document_store() -> None:
    stored = StoredDocument(
        id=7,
        name="informe renombrado",
        original_filename="informe.pdf",
        checksum="a" * 64,
        file_size=1024,
        page_count=3,
        extracted_text="# Informe",
        is_processed=True,
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
    )

    document = ProcessedDocument.from_stored(stored)

    assert document == ProcessedDocument(
        document_id=7,
        name="informe renombrado",
        filename="informe.pdf",
        size_bytes=1024,
        checksum="a" * 64,
        page_count=3,
        is_processed=True,
        extracted_text="# Informe",
        processed_at=CREATED_AT,
        # Después de un PUT, updated_at muestra cuándo cambió el documento.
        updated_at=UPDATED_AT,
    )
    assert document.updated_at == UPDATED_AT
