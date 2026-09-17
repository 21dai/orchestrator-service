"""Flujo completo del endpoint con pdf-extractext simulado por respx."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import get_settings

PDF_CONTENT = b"%PDF-1.4\n%contenido de prueba"
DOCUMENTS_URL = f"{get_settings().pdf_extract_base_url}/api/v1/documents"

EXTRACT_RESPONSE = {
    "id": 7,
    "name": "informe",
    "original_filename": "informe.pdf",
    "file_size": len(PDF_CONTENT),
    "checksum": "a" * 64,
    "extracted_text": "Texto extraido",
    "is_processed": True,
    "created_at": "2026-09-17T10:00:00Z",
    "updated_at": "2026-09-17T10:00:00Z",
}


def post_pdf(client: TestClient, **form: str) -> httpx.Response:
    return client.post(
        "/api/v1/documents",
        data=form,
        files={"file": ("informe.pdf", PDF_CONTENT, "application/pdf")},
    )


@respx.mock
def test_post_documents_orquesta_la_extraccion_y_devuelve_el_resultado(
    client: TestClient,
) -> None:
    route = respx.post(DOCUMENTS_URL).mock(return_value=httpx.Response(201, json=EXTRACT_RESPONSE))

    response = post_pdf(client, name="informe")

    assert route.called
    assert response.status_code == 201
    assert response.json() == {
        "document_id": 7,
        "name": "informe",
        "filename": "informe.pdf",
        "size_bytes": len(PDF_CONTENT),
        "checksum": "a" * 64,
        "is_processed": True,
        "extracted_text": "Texto extraido",
        "processed_at": "2026-09-17T10:00:00Z",
    }


@respx.mock
def test_post_documents_sin_nombre_usa_el_nombre_del_archivo(client: TestClient) -> None:
    route = respx.post(DOCUMENTS_URL).mock(return_value=httpx.Response(201, json=EXTRACT_RESPONSE))

    response = post_pdf(client)

    assert response.status_code == 201
    assert b'name="name"\r\n\r\ninforme' in route.calls.last.request.content


@respx.mock
def test_post_documents_envia_el_nombre_normalizado_sin_espacios(client: TestClient) -> None:
    route = respx.post(DOCUMENTS_URL).mock(return_value=httpx.Response(201, json=EXTRACT_RESPONSE))

    response = post_pdf(client, name="  informe final  ")

    assert response.status_code == 201
    assert b'name="name"\r\n\r\ninforme final' in route.calls.last.request.content


def test_post_documents_sin_archivo_devuelve_422(client: TestClient) -> None:
    response = client.post("/api/v1/documents")

    assert response.status_code == 422


def test_post_documents_rechaza_archivo_no_pdf_con_problem_details(client: TestClient) -> None:
    response = client.post(
        "/api/v1/documents",
        files={"file": ("notas.txt", b"texto plano", "text/plain")},
    )

    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Bad Request"
    assert body["status"] == 400
    assert "extensión .pdf" in body["detail"]
    assert body["instance"].endswith("/api/v1/documents")


def test_post_documents_rechaza_pdf_con_contenido_invalido(client: TestClient) -> None:
    response = client.post(
        "/api/v1/documents",
        files={"file": ("falso.pdf", b"no soy un pdf", "application/pdf")},
    )

    assert response.status_code == 400
    assert "no corresponde a un PDF" in response.json()["detail"]


@respx.mock
def test_post_documents_propaga_el_rechazo_de_pdf_extractext(client: TestClient) -> None:
    respx.post(DOCUMENTS_URL).mock(
        return_value=httpx.Response(
            400, json={"detail": "Ya existe un documento con el mismo checksum"}
        )
    )

    response = post_pdf(client, name="informe")

    assert response.status_code == 400
    assert response.json()["detail"] == "Ya existe un documento con el mismo checksum"


@respx.mock
def test_post_documents_informa_un_5xx_de_pdf_extractext(client: TestClient) -> None:
    respx.post(DOCUMENTS_URL).mock(return_value=httpx.Response(500, text="boom"))

    response = post_pdf(client, name="informe")

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    assert "500" in response.json()["detail"]


@respx.mock
def test_post_documents_informa_una_respuesta_invalida_de_pdf_extractext(
    client: TestClient,
) -> None:
    respx.post(DOCUMENTS_URL).mock(return_value=httpx.Response(201, json={"id": "x"}))

    response = post_pdf(client, name="informe")

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize(
    ("side_effect", "expected_status"),
    [(httpx.ConnectError, 503), (httpx.ReadTimeout, 504)],
)
@respx.mock
def test_post_documents_informa_fallos_de_pdf_extractext(
    client: TestClient, side_effect: type[Exception], expected_status: int
) -> None:
    respx.post(DOCUMENTS_URL).mock(side_effect=side_effect)

    response = post_pdf(client, name="informe")

    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/problem+json"
