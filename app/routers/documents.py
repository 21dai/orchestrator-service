from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from app.dependencies import DocumentServiceDep
from app.schemas.documents import ReceivedDocument

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

PdfFile = Annotated[UploadFile, File(description="Archivo PDF a procesar.")]


@router.post("", response_model=ReceivedDocument)
async def upload_document(service: DocumentServiceDep, file: PdfFile) -> ReceivedDocument:
    """Recibe un PDF (multipart/form-data, campo `file`) y delega en el service."""
    content = await file.read()

    return await service.receive_pdf(
        filename=file.filename or "",
        content_type=file.content_type,
        content=content,
    )
