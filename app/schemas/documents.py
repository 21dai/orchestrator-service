from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.pdf_extract import ExtractedDocument


@dataclass(frozen=True, slots=True)
class PdfDocument:
    """Representación interna de un PDF ya validado (ver ADR-0001).

    Es el objeto que circula entre service y client. Inmutable y sin
    dependencia de FastAPI, para que ambas capas se prueben con `bytes` de
    fixture. El contenido viaja tal cual llegó: no se transforma.
    """

    filename: str
    content_type: str
    content: bytes

    @property
    def size_bytes(self) -> int:
        return len(self.content)


class ProcessedDocument(BaseModel):
    """Respuesta pública del orquestador: el PDF ya procesado por pdf-extractext.

    Traduce el contrato del microservicio hoja a los nombres del orquestador
    (Blueprint, §4: no se exponen modelos internos sin traducción).
    """

    document_id: int = Field(description="Identificador del documento en pdf-extractext.")
    name: str = Field(description="Nombre con el que se registró el documento.")
    filename: str = Field(description="Nombre original del archivo subido.")
    size_bytes: int = Field(ge=0, description="Tamaño del archivo en bytes.")
    checksum: str = Field(description="SHA-256 del archivo, calculado por pdf-extractext.")
    is_processed: bool = Field(description="Si el texto ya fue extraído.")
    extracted_text: str | None = Field(default=None, description="Texto extraído del PDF.")
    processed_at: datetime = Field(
        description="Momento en que pdf-extractext registró el documento."
    )

    @classmethod
    def from_extracted(cls, extracted: ExtractedDocument) -> ProcessedDocument:
        return cls(
            document_id=extracted.id,
            name=extracted.name,
            filename=extracted.original_filename,
            size_bytes=extracted.file_size,
            checksum=extracted.checksum,
            is_processed=extracted.is_processed,
            extracted_text=extracted.extracted_text,
            processed_at=extracted.created_at,
        )
