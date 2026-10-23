from pathlib import PurePath
from typing import Protocol

from app.exceptions import InvalidDocumentNameError
from app.schemas.document_store import NewDocument, StoredDocument
from app.schemas.documents import PdfDocument, ProcessedDocument
from app.schemas.pdf_extract import ExtractionResult

#: Largo máximo del nombre que acepta document-store.
MAX_NAME_LENGTH = 255


class PdfExtractGateway(Protocol):
    """Lo que el service necesita del client de pdf-extractext (DIP)."""

    async def extract(self, document: PdfDocument) -> ExtractionResult: ...


class DocumentStoreGateway(Protocol):
    """Lo que el service necesita del client de document-store (DIP)."""

    async def create_document(self, document: NewDocument) -> StoredDocument: ...


class DocumentService:
    """Orquesta el procesamiento de un PDF ya validado y convertido.

    Coordina los dos microservicios: pdf-extractext extrae el texto y
    document-store lo guarda. El orquestador no persiste nada ni extrae
    texto: solo arma el documento (nombre y checksum) y traduce el resultado
    a la respuesta pública. Los fallos de los servicios llegan como
    excepciones de dominio de cada client y se dejan propagar para que
    `app/errors.py` las traduzca a HTTP.
    """

    def __init__(
        self, pdf_extract_client: PdfExtractGateway, document_store_client: DocumentStoreGateway
    ) -> None:
        self._pdf_extract = pdf_extract_client
        self._document_store = document_store_client

    async def process_pdf(self, document: PdfDocument, name: str | None) -> ProcessedDocument:
        """Extrae el texto del PDF y lo guarda en document-store.

        Si la extracción falla no se guarda nada. No hace falta compensar
        nada si falla el guardado: `/extract` no tiene estado, así que no
        queda nada a medias en pdf-extractext (no es una Saga).
        """
        resolved_name = self._resolve_name(document, name)
        extraction = await self._pdf_extract.extract(document)
        stored = await self._document_store.create_document(
            NewDocument(
                name=resolved_name,
                original_filename=document.filename,
                checksum=document.checksum,
                file_size=document.size_bytes,
                page_count=extraction.page_count,
                extracted_text=extraction.content,
                is_processed=True,
            )
        )
        return ProcessedDocument.from_stored(stored)

    @staticmethod
    def _resolve_name(document: PdfDocument, name: str | None) -> str:
        """document-store exige `name`; si no viene, se usa el archivo sin extensión.

        Se valida antes de extraer, para no gastar una extracción en un
        documento que document-store va a rechazar.
        """
        resolved = name.strip() if name and name.strip() else PurePath(document.filename).stem
        if len(resolved) > MAX_NAME_LENGTH:
            raise InvalidDocumentNameError(
                f"El nombre del documento no puede superar los {MAX_NAME_LENGTH} caracteres."
            )
        return resolved
