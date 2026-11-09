from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request, status

from app.dependencies import DocumentServiceDep, SettingsDep
from app.schemas.documents import DocumentUpdate, ProcessedDocument
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


# --- CRUD: delega en document-store ------------------------------------------

#: Id del documento en la ruta: entero positivo (si no, 422 sin llamar a document-store).
DocumentId = Annotated[int, Path(gt=0, description="Id del documento en document-store.")]

#: Errores comunes a todas las operaciones del CRUD, con Problem Details (RFC 9457).
STORE_ERRORS: dict[int | str, dict[str, Any]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: _problem(
        "Pedido inválido: id que no es un entero positivo, skip o limit fuera de rango, "
        "o nombre vacío, de más de 255 caracteres o con campos extra."
    ),
    status.HTTP_502_BAD_GATEWAY: _problem("document-store respondió algo inválido."),
    status.HTTP_503_SERVICE_UNAVAILABLE: _problem(
        "document-store no está disponible o está saturado "
        "(con Retry-After cuando se sabe cuándo reintentar)."
    ),
    status.HTTP_504_GATEWAY_TIMEOUT: _problem("document-store no respondió a tiempo."),
}

#: Las operaciones sobre un id, además, responden 404 si no existe.
DOCUMENT_ERRORS: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: _problem("No existe un documento con ese id."),
    **STORE_ERRORS,
}


@router.get("", response_model=list[ProcessedDocument], responses=STORE_ERRORS)
async def list_documents(
    service: DocumentServiceDep,
    skip: int = Query(default=0, ge=0, description="Documentos a saltear."),
    limit: int = Query(default=10, ge=1, le=100, description="Máximo de documentos (1 a 100)."),
) -> list[ProcessedDocument]:
    """Lista los documentos guardados en document-store, en orden de id ascendente.
    Lista vacía si no hay documentos en esa página."""
    return await service.list_documents(skip=skip, limit=limit)


@router.get("/{document_id}", response_model=ProcessedDocument, responses=DOCUMENT_ERRORS)
async def get_document(document_id: DocumentId, service: DocumentServiceDep) -> ProcessedDocument:
    """Devuelve un documento guardado, con su texto extraído."""
    return await service.get_document(document_id)


@router.put("/{document_id}", response_model=ProcessedDocument, responses=DOCUMENT_ERRORS)
async def update_document(
    document_id: DocumentId, body: DocumentUpdate, service: DocumentServiceDep
) -> ProcessedDocument:
    """Renombra un documento (lo único editable) y lo devuelve con `updated_at` actualizado."""
    return await service.update_document(document_id, name=body.name)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, responses=DOCUMENT_ERRORS)
async def delete_document(document_id: DocumentId, service: DocumentServiceDep) -> None:
    """Borra un documento de document-store. Responde 204 sin cuerpo."""
    await service.delete_document(document_id)
