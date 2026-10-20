"""Lectura del multipart recibido en memoria, cortada apenas supera el límite.

Por qué no se usa `UploadFile` de FastAPI directamente:

- Starlette pasa a un archivo temporal en disco toda parte de más de 1 MB, y
  el ADR-0001 decidió que el PDF viaja como `bytes` en memoria, sin archivos
  temporales. Acá el umbral para pasar a disco es el tamaño máximo aceptado,
  así que todo lo que se acepta queda en memoria.
- Sin límite, un archivo enorme se leía entero antes de validarlo. Ahora se
  rechaza con 413 por `Content-Length` si el cliente lo manda, y si no, apenas
  el body pasa del límite.

Es la misma técnica que usa pdf-extractext (`app/api/uploads.py`).
"""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from python_multipart.exceptions import FormParserError
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.exceptions import InvalidPdfError, PdfTooLargeError

# Margen para lo que agrega multipart alrededor del PDF (boundaries, headers de
# cada parte y el campo `name`): el límite exacto se aplica al archivo.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


def _reject_declared_size(request: Request, max_body_bytes: int) -> None:
    """Rechaza por `Content-Length` antes de leer nada, si el cliente lo manda."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_body_bytes:
        raise PdfTooLargeError(max_body_bytes - MULTIPART_OVERHEAD_BYTES)


async def _limited(stream: AsyncIterator[bytes], max_body_bytes: int) -> AsyncGenerator[bytes]:
    """Deja pasar los pedazos del body y corta con 413 apenas supera el límite."""
    received = 0
    async for chunk in stream:
        received += len(chunk)
        if received > max_body_bytes:
            raise PdfTooLargeError(max_body_bytes - MULTIPART_OVERHEAD_BYTES)
        yield chunk


def _is_multipart(request: Request) -> bool:
    return request.headers.get("content-type", "").startswith("multipart/form-data")


@asynccontextmanager
async def multipart_form(request: Request, max_file_bytes: int) -> AsyncIterator[FormData]:
    """Lee el multipart en memoria y lo cierra al terminar el bloque.

    Un body que no es multipart da un formulario vacío (y `form_file` responde
    422, como FastAPI). Lanza `PdfTooLargeError` si el body supera el límite
    del archivo más el margen, e `InvalidPdfError` si el multipart está mal
    formado.
    """
    if not _is_multipart(request):
        yield FormData()
        return

    limit = max_file_bytes + MULTIPART_OVERHEAD_BYTES
    _reject_declared_size(request, limit)
    parser = MultiPartParser(
        request.headers, _limited(request.stream(), limit), max_files=1, max_fields=10
    )
    # Ninguna parte supera el límite (lo corta _limited): todas quedan en memoria.
    parser.spool_max_size = limit
    try:
        form = await parser.parse()
    except (MultiPartException, FormParserError) as exc:
        # MultiPartException: límites de Starlette; FormParserError: body que
        # no respeta el formato multipart (lo lanza python-multipart).
        raise InvalidPdfError(f"El multipart recibido no es válido: {exc}") from exc
    try:
        yield form
    finally:
        await form.close()


def form_file(form: FormData, field: str) -> UploadFile:
    """El archivo del campo `field`, o un 422 si no vino (igual que FastAPI)."""
    upload = form.get(field)
    if not isinstance(upload, UploadFile):
        raise RequestValidationError(
            [{"type": "missing", "loc": ("body", field), "msg": "Field required", "input": None}]
        )
    return upload


def form_text(form: FormData, field: str) -> str | None:
    """El valor de un campo de texto opcional, o None si no vino."""
    value = form.get(field)
    return value if isinstance(value, str) else None
