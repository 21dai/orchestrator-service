from fastapi import APIRouter, Request, status

from app.dependencies import DocumentServiceDep, SettingsDep
from app.schemas.documents import ProcessedDocument
from app.services.pdf_loader import load_pdf
from app.uploads import form_file, form_text, multipart_form

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

# El multipart se lee a mano (ver app/uploads.py) para que el PDF no pase por
# disco y se corte apenas supera el límite; como FastAPI ya no lo infiere de
# los parámetros, el formulario se documenta acá para /docs.
UPLOAD_FORM_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {
                            "type": "string",
                            "format": "binary",
                            "description": "Archivo PDF a procesar (hasta 10 MB).",
                        },
                        "name": {
                            "type": "string",
                            "description": (
                                "Nombre del documento. Si se omite, se usa el nombre del archivo."
                            ),
                        },
                    },
                }
            }
        },
    }
}


@router.post(
    "",
    response_model=ProcessedDocument,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=UPLOAD_FORM_OPENAPI,
)
async def upload_document(
    request: Request, service: DocumentServiceDep, settings: SettingsDep
) -> ProcessedDocument:
    """Recibe un PDF (multipart/form-data: `file` y opcionalmente `name`),
    lo envía a pdf-extractext y devuelve el documento con su texto extraído."""
    async with multipart_form(request, settings.max_pdf_size_bytes) as form:
        document = await load_pdf(form_file(form, "file"), settings.max_pdf_size_bytes)
        name = form_text(form, "name")

    return await service.process_pdf(document, name=name)
