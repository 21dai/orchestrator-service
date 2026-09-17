from pathlib import PurePath
from typing import Protocol

from app.schemas.documents import PdfDocument, ProcessedDocument
from app.schemas.pdf_extract import ExtractedDocument


class PdfExtractGateway(Protocol):
    """Lo que el service necesita del client de pdf-extractext (DIP)."""

    async def create_document(self, document: PdfDocument, name: str) -> ExtractedDocument: ...


class DocumentService:
    """Orquesta el procesamiento de un PDF ya validado y convertido.

    Recibe un `PdfDocument`, decide el nombre con el que se registra, lo envía
    a pdf-extractext y traduce el resultado a la respuesta pública. No
    persiste nada: el orquestador no tiene estado. Los fallos del hoja llegan
    como excepciones de dominio del client y se dejan propagar para que
    `app/errors.py` las traduzca a HTTP.
    """

    def __init__(self, pdf_extract_client: PdfExtractGateway) -> None:
        self._pdf_extract = pdf_extract_client

    async def process_pdf(self, document: PdfDocument, name: str | None) -> ProcessedDocument:
        extracted = await self._pdf_extract.create_document(
            document, name=self._resolve_name(document, name)
        )
        return ProcessedDocument.from_extracted(extracted)

    @staticmethod
    def _resolve_name(document: PdfDocument, name: str | None) -> str:
        """pdf-extractext exige `name`; si no viene, se usa el archivo sin extensión."""
        if name and name.strip():
            return name.strip()
        return PurePath(document.filename).stem
