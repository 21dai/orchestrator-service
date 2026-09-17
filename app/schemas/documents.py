from dataclasses import dataclass

from pydantic import BaseModel, Field


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


class ReceivedDocument(BaseModel):
    """Metadatos del PDF recibido por el orquestador (respuesta pública)."""

    filename: str = Field(description="Nombre original del archivo subido.")
    content_type: str | None = Field(
        default=None, description="Tipo MIME declarado por el cliente."
    )
    size_bytes: int = Field(ge=0, description="Tamaño del archivo en bytes.")

    @classmethod
    def from_pdf(cls, document: PdfDocument) -> ReceivedDocument:
        return cls(
            filename=document.filename,
            content_type=document.content_type,
            size_bytes=document.size_bytes,
        )
