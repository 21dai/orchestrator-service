from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status

from app.dependencies import DocumentServiceDep
from app.schemas.documents import ProcessedDocument
from app.services.pdf_loader import load_pdf

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

PdfFile = Annotated[UploadFile, File(description="Archivo PDF a procesar.")]
DocumentName = Annotated[
    str | None,
    Form(description="Nombre del documento. Si se omite, se usa el nombre del archivo."),
]


@router.post("", response_model=ProcessedDocument, status_code=status.HTTP_201_CREATED)
async def upload_document(
    service: DocumentServiceDep, file: PdfFile, name: DocumentName = None
) -> ProcessedDocument:
    """Recibe un PDF (multipart/form-data: `file` y opcionalmente `name`),
    lo envía a pdf-extractext y devuelve el documento con su texto extraído."""
    document = await load_pdf(file)

    return await service.process_pdf(document, name=name)
