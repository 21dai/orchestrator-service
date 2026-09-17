"""Wiring de inyección de dependencias: providers para routers y services."""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.clients.pdf_extract_client import PdfExtractClient
from app.config import Settings, get_settings
from app.services.document_service import DocumentService

SettingsDep = Annotated[Settings, Depends(get_settings)]


@lru_cache
def get_pdf_extract_client() -> PdfExtractClient:
    """Un único client (y pool de conexiones) por proceso, no por request.

    Se cierra en el lifespan de la app (`app/main.py`), que además limpia
    esta caché para que un arranque posterior cree un client nuevo.
    """
    settings = get_settings()
    return PdfExtractClient(
        base_url=settings.pdf_extract_base_url,
        timeout_seconds=settings.http_timeout_seconds,
    )


async def close_pdf_extract_client() -> None:
    if get_pdf_extract_client.cache_info().currsize:
        await get_pdf_extract_client().aclose()
        get_pdf_extract_client.cache_clear()


PdfExtractClientDep = Annotated[PdfExtractClient, Depends(get_pdf_extract_client)]


def get_document_service(pdf_extract_client: PdfExtractClientDep) -> DocumentService:
    return DocumentService(pdf_extract_client=pdf_extract_client)


DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
