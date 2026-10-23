from typing import Any

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


def _problem(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/problem+json": {"schema": {"type": "object"}}},
    }


# Errores que puede responder el endpoint, todos con Problem Details (RFC 9457).
UPLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: _problem(
        "El archivo no es un PDF válido, el nombre es demasiado largo o "
        "pdf-extractext no pudo leer el PDF."
    ),
    status.HTTP_409_CONFLICT: _problem("El PDF ya estaba guardado (mismo checksum SHA-256)."),
    status.HTTP_413_CONTENT_TOO_LARGE: _problem("El PDF supera el tamaño máximo."),
    status.HTTP_502_BAD_GATEWAY: _problem(
        "pdf-extractext o document-store respondió algo inválido."
    ),
    status.HTTP_503_SERVICE_UNAVAILABLE: _problem(
        "pdf-extractext o document-store no está disponible o está saturado "
        "(con Retry-After cuando se sabe cuándo reintentar)."
    ),
    status.HTTP_504_GATEWAY_TIMEOUT: _problem(
        "pdf-extractext o document-store no respondió a tiempo."
    ),
}


@router.post(
    "",
    response_model=ProcessedDocument,
    status_code=status.HTTP_201_CREATED,
    responses=UPLOAD_ERRORS,
    openapi_extra=UPLOAD_FORM_OPENAPI,
)
async def upload_document(
    request: Request, service: DocumentServiceDep, settings: SettingsDep
) -> ProcessedDocument:
    """Recibe un PDF (multipart/form-data: `file` y opcionalmente `name`),
    extrae su texto con pdf-extractext, lo guarda en document-store y devuelve
    el documento guardado."""
    async with multipart_form(request, settings.max_pdf_size_bytes) as form:
        document = await load_pdf(form_file(form, "file"), settings.max_pdf_size_bytes)
        name = form_text(form, "name")

    return await service.process_pdf(document, name=name)
