"""Flujo end-to-end del endpoint de documentos contra el stack desplegado."""

import hashlib
import os
from datetime import datetime
from pathlib import PurePath

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SYSTEM_TESTS") != "1",
    reason="Tests de sistema: requieren el stack desplegado (RUN_SYSTEM_TESTS=1).",
)


def test_flujo_completo_de_un_pdf_end_to_end(
    http: httpx.Client, pdf_file: tuple[str, bytes]
) -> None:
    filename, content = pdf_file

    response = http.post(
        "/api/v1/documents",
        data={"name": "sistema"},
        files={"file": (filename, content, "application/pdf")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "sistema"
    assert body["filename"] == filename
    assert body["size_bytes"] == len(content)
    # Integridad de punta a punta: el checksum registrado es el del archivo enviado.
    assert body["checksum"] == hashlib.sha256(content).hexdigest()
    assert isinstance(body["document_id"], int)
    assert body["document_id"] > 0
    assert isinstance(body["is_processed"], bool)
    assert body["extracted_text"] is None or isinstance(body["extracted_text"], str)
    # Fecha ISO 8601 válida (lanza si no lo es).
    datetime.fromisoformat(body["processed_at"])


def test_flujo_sin_nombre_usa_el_nombre_del_archivo(
    http: httpx.Client, pdf_file: tuple[str, bytes]
) -> None:
    filename, content = pdf_file

    response = http.post(
        "/api/v1/documents",
        files={"file": (filename, content, "application/pdf")},
    )

    assert response.status_code == 201, response.text
    assert response.json()["name"] == PurePath(filename).stem


def test_flujo_rechaza_un_archivo_que_no_es_pdf(http: httpx.Client) -> None:
    response = http.post(
        "/api/v1/documents",
        files={"file": ("notas.txt", b"texto plano", "text/plain")},
    )

    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 400
    assert "extensión .pdf" in body["detail"]
