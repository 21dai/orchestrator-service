"""Flujo end-to-end del endpoint de documentos contra el stack desplegado.

Cliente → orchestrator → pdf-extractext (`/extract`) → document-store → MongoDB,
todo real (ADR-0003). Los fallos de los servicios que no se pueden provocar sin
tocar el stack están en `test_fault_tolerance_system.py` y en
`tests/integration/test_documents.py`.
"""

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

DOCUMENTS = "/api/v1/documents"
PROBLEM_JSON = "application/problem+json"


def post_pdf(http: httpx.Client, pdf: tuple[str, bytes], **form: str) -> httpx.Response:
    filename, content = pdf
    return http.post(DOCUMENTS, data=form, files={"file": (filename, content, "application/pdf")})


def assert_problem(response: httpx.Response, status: int) -> dict[str, object]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    body = dict(response.json())
    assert body["status"] == status
    assert body["detail"]
    return body


def test_flujo_completo_extrae_guarda_y_devuelve_el_documento(
    http: httpx.Client, pdf_file: tuple[str, bytes]
) -> None:
    filename, content = pdf_file

    response = post_pdf(http, pdf_file, name="sistema")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "sistema"
    assert body["filename"] == filename
    assert body["size_bytes"] == len(content)
    # Integridad de punta a punta: el checksum que guardó document-store es el
    # SHA-256 de los bytes enviados, calculado por el orquestador.
    assert body["checksum"] == hashlib.sha256(content).hexdigest()
    # Lo que devolvió pdf-extractext: texto (aunque la página esté vacía) y páginas.
    assert body["page_count"] >= 1
    assert body["is_processed"] is True
    assert isinstance(body["extracted_text"], str)
    # El id lo asigna document-store al guardarlo.
    assert isinstance(body["document_id"], int)
    assert body["document_id"] > 0
    # Fecha ISO 8601 válida (lanza si no lo es).
    datetime.fromisoformat(body["processed_at"])


def test_flujo_sin_nombre_usa_el_nombre_del_archivo(
    http: httpx.Client, pdf_file: tuple[str, bytes]
) -> None:
    filename, _ = pdf_file

    response = post_pdf(http, pdf_file)

    assert response.status_code == 201, response.text
    assert response.json()["name"] == PurePath(filename).stem


def test_el_mismo_pdf_dos_veces_responde_409_con_problem_details(
    http: httpx.Client, pdf_file: tuple[str, bytes]
) -> None:
    checksum = hashlib.sha256(pdf_file[1]).hexdigest()

    first = post_pdf(http, pdf_file, name="original")
    second = post_pdf(http, pdf_file, name="copia")

    # El primero quedó persistido en document-store: por eso el segundo choca
    # con su checksum y el orquestador propaga el 409.
    assert first.status_code == 201, first.text
    body = assert_problem(second, 409)
    assert body["title"] == "Conflict"
    assert checksum in str(body["detail"])


def test_flujo_rechaza_un_archivo_que_no_es_pdf(http: httpx.Client) -> None:
    response = http.post(
        DOCUMENTS,
        files={"file": ("notas.txt", b"texto plano", "text/plain")},
    )

    body = assert_problem(response, 400)
    assert "extensión .pdf" in str(body["detail"])


def test_un_pdf_danado_responde_400_y_no_se_guarda(
    http: httpx.Client,
    damaged_pdf_file: tuple[str, bytes],
    otro_pdf_file: tuple[str, bytes],
) -> None:
    # pdf-extractext responde 422 si PDFium no puede abrir el PDF; para el
    # orquestador es un 4xx del hoja (PdfExtractRejectedError), que
    # app/errors.py traduce a 400 reenviando el detail de pdf-extractext.
    first = post_pdf(http, damaged_pdf_file, name="danado")
    first_body = assert_problem(first, 400)

    # El stack sigue sano: un PDF válido distinto se guarda.
    valid = post_pdf(http, otro_pdf_file, name="valido")
    assert valid.status_code == 201, valid.text

    # Reenviar el dañado da el mismo error y no un 409: no quedó nada guardado
    # con su checksum.
    again = post_pdf(http, damaged_pdf_file, name="danado")
    again_body = assert_problem(again, 400)
    assert again_body["detail"] == first_body["detail"]
