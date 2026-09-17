from pydantic import BaseModel, Field


class ReceivedDocument(BaseModel):
    """Metadatos del PDF recibido por el orquestador."""

    filename: str = Field(description="Nombre original del archivo subido.")
    content_type: str | None = Field(
        default=None, description="Tipo MIME declarado por el cliente."
    )
    size_bytes: int = Field(ge=0, description="Tamaño del archivo en bytes.")
