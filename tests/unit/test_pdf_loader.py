from io import BytesIO

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.exceptions import InvalidPdfError
from app.schemas.documents import PdfDocument
from app.services.pdf_loader import load_pdf

PDF_CONTENT = b"%PDF-1.4\n%contenido de prueba"


def make_upload(filename: str, content_type: str, content: bytes) -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


async def test_load_pdf_convierte_el_upload_en_pdf_document() -> None:
    upload = make_upload("informe.pdf", "application/pdf", PDF_CONTENT)

    document = await load_pdf(upload)

    assert document == PdfDocument(
        filename="informe.pdf",
        content_type="application/pdf",
        content=PDF_CONTENT,
    )


async def test_load_pdf_lee_el_contenido_completo() -> None:
    content = b"%PDF-1.4\n" + b"x" * 100_000
    upload = make_upload("grande.pdf", "application/pdf", content)

    document = await load_pdf(upload)

    assert document.size_bytes == len(content)
    assert document.content == content


async def test_load_pdf_rechaza_un_archivo_invalido() -> None:
    upload = make_upload("notas.txt", "text/plain", b"texto plano")

    with pytest.raises(InvalidPdfError):
        await load_pdf(upload)
