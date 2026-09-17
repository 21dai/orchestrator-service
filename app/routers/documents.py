from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from app.dependencies import DocumentServiceDep
from app.schemas.documents import ReceivedDocument
from app.services.pdf_loader import load_pdf

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

PdfFile = Annotated[UploadFile, File(description="Archivo PDF a procesar.")]


@router.post("", response_model=ReceivedDocument)
async def upload_document(service: DocumentServiceDep, file: PdfFile) -> ReceivedDocument:
    """Recibe un PDF (multipart/form-data, campo `file`) y delega en el service."""
    document = await load_pdf(file)

    return await service.receive_pdf(document)
