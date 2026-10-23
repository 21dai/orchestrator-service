"""Modelos de la respuesta de pdf-extractext (contrato del microservicio hoja).

Son internos al orquestador: lo que se expone al cliente final se traduce en
`app/schemas/documents.py`, nunca se devuelven tal cual (Blueprint, §4).
"""

from pydantic import BaseModel, Field


class ExtractionResult(BaseModel):
    """Respuesta de `POST /extract`: el texto en Markdown y la cantidad de páginas."""

    content: str
    page_count: int = Field(ge=0)
