from fastapi import UploadFile

from app.schemas.documents import PdfDocument
from app.services.pdf_validator import PDF_CONTENT_TYPE, validate_pdf


async def load_pdf(upload: UploadFile) -> PdfDocument:
    """Lee el archivo subido, lo valida y lo convierte a `PdfDocument`.

    Es el único punto donde el `UploadFile` de FastAPI se traduce a la
    representación interna (`bytes` en memoria, ADR-0001). Lanza
    `InvalidPdfError` si el archivo no es un PDF.
    """
    content = await upload.read()
    filename = upload.filename or ""

    validate_pdf(filename=filename, content_type=upload.content_type, content=content)

    # validate_pdf ya garantizó que el tipo MIME es exactamente application/pdf.
    return PdfDocument(filename=filename, content_type=PDF_CONTENT_TYPE, content=content)
