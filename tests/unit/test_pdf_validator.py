import pytest

from app.exceptions import InvalidPdfError
from app.services.pdf_validator import validate_pdf

PDF_CONTENT = b"%PDF-1.4\n%contenido de prueba"


def test_validate_pdf_acepta_un_pdf_valido() -> None:
    validate_pdf(
        filename="informe.pdf",
        content_type="application/pdf",
        content=PDF_CONTENT,
    )


def test_validate_pdf_acepta_extension_en_mayusculas() -> None:
    validate_pdf(
        filename="INFORME.PDF",
        content_type="application/pdf",
        content=PDF_CONTENT,
    )


@pytest.mark.parametrize("filename", ["informe.txt", "informe", "informe.pdf.exe", ""])
def test_validate_pdf_rechaza_extension_invalida(filename: str) -> None:
    with pytest.raises(InvalidPdfError, match="extensión .pdf"):
        validate_pdf(
            filename=filename,
            content_type="application/pdf",
            content=PDF_CONTENT,
        )


@pytest.mark.parametrize("content_type", ["text/plain", "application/octet-stream", None])
def test_validate_pdf_rechaza_content_type_invalido(content_type: str | None) -> None:
    with pytest.raises(InvalidPdfError, match="application/pdf"):
        validate_pdf(
            filename="informe.pdf",
            content_type=content_type,
            content=PDF_CONTENT,
        )


def test_validate_pdf_rechaza_archivo_vacio() -> None:
    with pytest.raises(InvalidPdfError, match="vacío"):
        validate_pdf(
            filename="informe.pdf",
            content_type="application/pdf",
            content=b"",
        )


def test_validate_pdf_rechaza_contenido_que_no_es_pdf() -> None:
    with pytest.raises(InvalidPdfError, match="no corresponde a un PDF"):
        validate_pdf(
            filename="informe.pdf",
            content_type="application/pdf",
            content=b"esto es texto plano disfrazado",
        )
