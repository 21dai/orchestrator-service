import dataclasses
import hashlib

import pytest

from app.schemas.documents import PdfDocument

PDF_CONTENT = b"%PDF-1.4\n%contenido de prueba"


def test_pdf_document_expone_tamanio_en_bytes() -> None:
    document = PdfDocument(
        filename="informe.pdf",
        content_type="application/pdf",
        content=PDF_CONTENT,
    )

    assert document.size_bytes == len(PDF_CONTENT)


def test_pdf_document_es_inmutable() -> None:
    document = PdfDocument(
        filename="informe.pdf",
        content_type="application/pdf",
        content=PDF_CONTENT,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        document.filename = "otro.pdf"  # type: ignore[misc]


def test_pdf_document_calcula_el_checksum_sha256_de_su_contenido() -> None:
    document = PdfDocument(
        filename="informe.pdf",
        content_type="application/pdf",
        content=PDF_CONTENT,
    )

    assert document.checksum == hashlib.sha256(PDF_CONTENT).hexdigest()
    assert len(document.checksum) == 64
