"""Wiring de inyección de dependencias: providers para routers y services."""

from typing import Annotated

from fastapi import Depends

from app.config import Settings, get_settings
from app.services.document_service import DocumentService

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_document_service() -> DocumentService:
    return DocumentService()


DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
