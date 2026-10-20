"""Lectura del multipart en memoria, con el límite de tamaño (ver `app/uploads.py`)."""

import tempfile
from collections.abc import Callable

import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.datastructures import FormData, UploadFile
from starlette.types import Message

from app.exceptions import InvalidPdfError, PdfTooLargeError
from app.uploads import MULTIPART_OVERHEAD_BYTES, form_file, form_text, multipart_form

BOUNDARY = "limite-de-prueba"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}".encode()


def multipart_body(content: bytes, name: str | None = None) -> bytes:
    parts = []
    if name is not None:
        disposition = 'Content-Disposition: form-data; name="name"'
        parts.append(f"--{BOUNDARY}\r\n{disposition}\r\n\r\n{name}\r\n".encode())
    parts.append(
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="file"; filename="informe.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{BOUNDARY}--\r\n".encode())
    return b"".join(parts)


def make_request(
    body: bytes,
    content_length: int | None = None,
    content_type: bytes = CONTENT_TYPE,
    chunk_size: int = 64 * 1024,
) -> tuple[Request, list[int]]:
    """Request que entrega el body en pedazos y registra cuántos bytes se leyeron."""
    headers = [(b"content-type", content_type)]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    chunks = [body[i : i + chunk_size] for i in range(0, len(body), chunk_size)] or [b""]
    read: list[int] = []

    async def receive() -> Message:
        chunk = chunks[len(read)]
        read.append(len(chunk))
        return {"type": "http.request", "body": chunk, "more_body": len(read) < len(chunks)}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/documents",
        "headers": headers,
        "query_string": b"",
    }
    return Request(scope, receive), read


@pytest.fixture
def disk_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falla si un upload pasa de memoria a un archivo temporal en disco."""

    def rollover(self: tempfile.SpooledTemporaryFile[bytes]) -> None:
        raise AssertionError("el upload se escribió en un archivo temporal")

    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover", rollover)


async def test_lee_un_pdf_de_mas_de_1_mb_sin_escribirlo_a_disco(
    disk_forbidden: Callable[[], None],
) -> None:
    # Starlette pasa a disco las partes de más de 1 MB; el PDF tiene que quedar en memoria.
    content = b"%PDF-1.4\n" + b"x" * (2 * 1024 * 1024)
    request, _ = make_request(multipart_body(content, name="informe"))

    async with multipart_form(request, max_file_bytes=10 * 1024 * 1024) as form:
        upload = form_file(form, "file")
        assert await upload.read() == content
        assert form_text(form, "name") == "informe"


async def test_rechaza_por_content_length_sin_leer_el_body() -> None:
    request, read = make_request(b"", content_length=50 * 1024 * 1024)

    with pytest.raises(PdfTooLargeError, match="tamaño máximo"):
        async with multipart_form(request, max_file_bytes=10 * 1024 * 1024):
            pass

    assert read == []


async def test_corta_el_body_apenas_supera_el_limite_aunque_no_declare_content_length() -> None:
    limit = 100 * 1024
    body = multipart_body(b"%PDF-1.4\n" + b"x" * (5 * 1024 * 1024))
    request, read = make_request(body, chunk_size=16 * 1024)

    with pytest.raises(PdfTooLargeError):
        async with multipart_form(request, max_file_bytes=limit):
            pass

    # Deja de leer en cuanto pasa el límite, no recibe los 5 MB.
    assert sum(read) <= limit + MULTIPART_OVERHEAD_BYTES + 16 * 1024


async def test_un_multipart_mal_formado_es_un_pdf_invalido() -> None:
    request, _ = make_request(b"esto no es un multipart")

    with pytest.raises(InvalidPdfError, match="multipart"):
        async with multipart_form(request, max_file_bytes=1024):
            pass


async def test_un_body_que_no_es_multipart_no_trae_el_archivo() -> None:
    request, _ = make_request(b'{"file": "x"}', content_type=b"application/json")

    async with multipart_form(request, max_file_bytes=1024) as form:
        with pytest.raises(RequestValidationError):
            form_file(form, "file")


def test_form_file_sin_el_campo_es_un_error_de_validacion() -> None:
    with pytest.raises(RequestValidationError) as caught:
        form_file(FormData(), "file")

    assert caught.value.errors()[0]["loc"] == ("body", "file")


def test_form_file_devuelve_el_archivo() -> None:
    upload = UploadFile(file=tempfile.SpooledTemporaryFile(), filename="a.pdf")

    assert form_file(FormData([("file", upload)]), "file") is upload


def test_form_text_es_opcional() -> None:
    assert form_text(FormData(), "name") is None
    assert form_text(FormData([("name", "informe")]), "name") == "informe"
