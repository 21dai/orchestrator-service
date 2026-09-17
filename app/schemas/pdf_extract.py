"""Modelos de la respuesta de pdf-extractext (contrato del microservicio hoja).

Son internos al orquestador: lo que se expone al cliente final se traduce en
`app/schemas/documents.py`, nunca se devuelven tal cual (Blueprint, §4).
"""

from datetime import datetime

from pydantic import BaseModel


class ExtractedDocument(BaseModel):
    """`DocumentResponse` de pdf-extractext v1.0.1."""

    id: int
    name: str
    original_filename: str
    file_size: int
    checksum: str
    extracted_text: str | None = None
    is_processed: bool
    created_at: datetime
    updated_at: datetime
