import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.document_store import StoredDocument


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

    @property
    def checksum(self) -> str:
        """SHA-256 hexadecimal de los bytes: identifica al PDF en document-store."""
        return hashlib.sha256(self.content).hexdigest()


#: Nombre de documento: de 1 a 255 caracteres sin contar los espacios de los
#: extremos, igual que en document-store.
DocumentName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class DocumentUpdate(BaseModel):
    """Body del `PUT /api/v1/documents/{id}`: lo único editable es el nombre.

    Mismas reglas que el `DocumentUpdate` de document-store (estricto y sin
    campos extra), validadas acá: un nombre inválido es un error del cliente
    (422), no una respuesta rara de document-store (502).
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    name: DocumentName = Field(description="Nuevo nombre del documento (1 a 255 caracteres).")


class ProcessedDocument(BaseModel):
    """Respuesta pública del orquestador: el PDF procesado y guardado.

    Traduce el contrato de document-store a los nombres del orquestador
    (Blueprint, §4: no se exponen modelos internos sin traducción).
    """

    document_id: int = Field(description="Identificador del documento en document-store.")
    name: str = Field(description="Nombre con el que se registró el documento.")
    filename: str = Field(description="Nombre original del archivo subido.")
    size_bytes: int = Field(ge=0, description="Tamaño del archivo en bytes.")
    checksum: str = Field(description="SHA-256 del archivo, calculado por el orquestador.")
    page_count: int = Field(ge=0, description="Cantidad de páginas del PDF.")
    is_processed: bool = Field(description="Si el texto ya fue extraído.")
    extracted_text: str = Field(description="Texto extraído del PDF, en Markdown.")
    processed_at: datetime = Field(description="Momento en que se guardó el documento.")
    updated_at: datetime = Field(
        description="Última modificación del documento (igual a processed_at si nunca se editó)."
    )

    @classmethod
    def from_stored(cls, stored: StoredDocument) -> ProcessedDocument:
        return cls(
            document_id=stored.id,
            name=stored.name,
            filename=stored.original_filename,
            size_bytes=stored.file_size,
            checksum=stored.checksum,
            page_count=stored.page_count,
            is_processed=stored.is_processed,
            extracted_text=stored.extracted_text,
            processed_at=stored.created_at,
            updated_at=stored.updated_at,
        )
