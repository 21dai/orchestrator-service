"""Modelos del contrato de document-store (`POST /api/v1/documents`).

Son internos al orquestador: lo que se expone al cliente final se traduce en
`app/schemas/documents.py`, nunca se devuelven tal cual (Blueprint, §4).
"""

from datetime import datetime

from pydantic import BaseModel


class NewDocument(BaseModel):
    """Body del alta: el PDF ya procesado, sin el archivo."""

    name: str
    original_filename: str
    checksum: str
    file_size: int
    page_count: int
    extracted_text: str
    is_processed: bool


class StoredDocument(NewDocument):
    """`DocumentResponse` de document-store: el documento guardado, con su id."""

    id: int
    created_at: datetime
    updated_at: datetime
