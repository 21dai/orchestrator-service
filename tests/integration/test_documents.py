"""Flujo completo del endpoint con pdf-extractext y document-store simulados por respx."""

import hashlib
import json
from collections.abc import Iterator

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app

PDF_CONTENT = b"%PDF-1.4\n%contenido de prueba"
CHECKSUM = hashlib.sha256(PDF_CONTENT).hexdigest()
EXTRACT_URL = f"{get_settings().pdf_extract_base_url}/extract"
STORE_URL = f"{get_settings().document_store_base_url}/api/v1/documents"

EXTRACT_RESPONSE = {"content": "# Informe\n\nTexto extraido", "page_count": 3}


def stored(request: httpx.Request) -> httpx.Response:
    """document-store simulado: devuelve lo recibido con id y fechas."""
    body = json.loads(request.content)
    return httpx.Response(
        201,
        json={
            **body,
            "id": 7,
            "created_at": "2026-10-23T10:00:00Z",
            "updated_at": "2026-10-23T10:00:00Z",
        },
    )


def problem(status: int, detail: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"type": "about:blank", "title": "Error", "status": status, "detail": detail},
        headers={"content-type": "application/problem+json"},
    )


def mock_services(
    extract: httpx.Response | None = None,
) -> tuple[respx.Route, respx.Route]:
    extract_route = respx.post(EXTRACT_URL).mock(
        return_value=extract or httpx.Response(200, json=EXTRACT_RESPONSE)
    )
    store_route = respx.post(STORE_URL).mock(side_effect=stored)
    return extract_route, store_route


def post_pdf(client: TestClient, content: bytes = PDF_CONTENT, **form: str) -> httpx.Response:
    return client.post(
        "/api/v1/documents",
        data=form,
        files={"file": ("informe.pdf", content, "application/pdf")},
    )


def sent_to_store(route: respx.Route) -> dict[str, object]:
    return dict(json.loads(route.calls.last.request.content))


@respx.mock
def test_post_documents_extrae_guarda_y_devuelve_el_documento(client: TestClient) -> None:
    extract_route, store_route = mock_services()

    response = post_pdf(client, name="informe")

    assert response.status_code == 201
    assert PDF_CONTENT in extract_route.calls.last.request.content
    assert sent_to_store(store_route) == {
        "name": "informe",
        "original_filename": "informe.pdf",
        "checksum": CHECKSUM,
        "file_size": len(PDF_CONTENT),
        "page_count": 3,
        "extracted_text": "# Informe\n\nTexto extraido",
        "is_processed": True,
    }
    assert response.json() == {
        "document_id": 7,
        "name": "informe",
        "filename": "informe.pdf",
        "size_bytes": len(PDF_CONTENT),
        "checksum": CHECKSUM,
        "page_count": 3,
        "is_processed": True,
        "extracted_text": "# Informe\n\nTexto extraido",
        "processed_at": "2026-10-23T10:00:00Z",
    }


@respx.mock
def test_post_documents_sin_nombre_usa_el_nombre_del_archivo(client: TestClient) -> None:
    _, store_route = mock_services()

    response = post_pdf(client)

    assert response.status_code == 201
    assert sent_to_store(store_route)["name"] == "informe"


@respx.mock
def test_post_documents_envia_el_nombre_normalizado_sin_espacios(client: TestClient) -> None:
    _, store_route = mock_services()

    response = post_pdf(client, name="  informe final  ")

    assert response.status_code == 201
    assert sent_to_store(store_route)["name"] == "informe final"


@respx.mock
def test_post_documents_rechaza_un_nombre_de_mas_de_255_caracteres_sin_llamar_a_nadie(
    client: TestClient,
) -> None:
    extract_route, store_route = mock_services()

    response = post_pdf(client, name="a" * 256)

    assert response.status_code == 400
    assert "255" in response.json()["detail"]
    assert not extract_route.called
    assert not store_route.called


@respx.mock
def test_post_documents_responde_409_si_el_pdf_ya_estaba_guardado(client: TestClient) -> None:
    respx.post(EXTRACT_URL).mock(return_value=httpx.Response(200, json=EXTRACT_RESPONSE))
    respx.post(STORE_URL).mock(
        return_value=problem(409, f"Ya existe un documento con el checksum {CHECKSUM}.")
    )

    response = post_pdf(client, name="informe")

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["title"] == "Conflict"
    assert CHECKSUM in body["detail"]


def test_post_documents_sin_archivo_devuelve_422(client: TestClient) -> None:
    response = client.post("/api/v1/documents")

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 422
    assert body["type"] == "about:blank"
    assert body["title"] == "Unprocessable Content"
    assert body["detail"]
    assert body["instance"].endswith("/api/v1/documents")


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


# --- Errores de pdf-extractext: nunca se guarda un documento incompleto ---


@respx.mock
def test_post_documents_propaga_el_rechazo_de_pdf_extractext_sin_guardar(
    client: TestClient,
) -> None:
    _, store_route = mock_services(extract=problem(400, "El PDF está dañado"))

    response = post_pdf(client, name="informe")

    assert response.status_code == 400
    assert response.json()["detail"] == "El PDF está dañado"
    assert not store_route.called


@pytest.mark.parametrize(
    ("extract", "expected_status"),
    [
        (httpx.Response(500, text="boom"), 502),
        (httpx.Response(200, json={"content": "sin page_count"}), 502),
        (httpx.Response(503, text="no available server"), 503),
    ],
    ids=["5xx", "respuesta-invalida", "sin-replicas"],
)
@respx.mock
def test_post_documents_informa_las_fallas_de_pdf_extractext_sin_guardar(
    client: TestClient, extract: httpx.Response, expected_status: int
) -> None:
    _, store_route = mock_services(extract=extract)

    response = post_pdf(client, name="informe")

    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/problem+json"
    assert not store_route.called


@pytest.mark.parametrize(
    ("side_effect", "expected_status"),
    [(httpx.ConnectError, 503), (httpx.ReadTimeout, 504)],
)
@respx.mock
def test_post_documents_informa_fallos_de_red_hacia_pdf_extractext(
    client: TestClient, side_effect: type[Exception], expected_status: int
) -> None:
    respx.post(EXTRACT_URL).mock(side_effect=side_effect)
    store_route = respx.post(STORE_URL).mock(side_effect=stored)

    response = post_pdf(client, name="informe")

    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/problem+json"
    assert not store_route.called


@respx.mock
def test_post_documents_reenvia_la_sobrecarga_de_pdf_extractext_con_retry_after(
    client: TestClient,
) -> None:
    mock_services(
        extract=httpx.Response(
            503, json={"detail": "Cola de extraccion llena"}, headers={"Retry-After": "3"}
        )
    )

    response = post_pdf(client, name="informe")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "3"
    assert response.headers["content-type"] == "application/problem+json"


# --- Errores de document-store ---


@pytest.mark.parametrize(
    ("store", "expected_status"),
    [
        # MongoDB caído: document-store responde 503 sin Retry-After.
        ({"return_value": problem(503, "La base de datos no está disponible")}, 503),
        ({"return_value": httpx.Response(500, text="boom")}, 502),
        # Un 400 es un contrato roto entre servicios, no un error del cliente.
        ({"return_value": problem(400, "Datos inválidos: checksum")}, 502),
        ({"side_effect": httpx.ConnectError("refused")}, 503),
        ({"side_effect": httpx.ReadTimeout("timeout")}, 504),
    ],
    ids=["sin-base", "5xx", "contrato-roto", "caido", "timeout"],
)
@respx.mock
def test_post_documents_informa_las_fallas_de_document_store(
    client: TestClient, store: dict[str, object], expected_status: int
) -> None:
    respx.post(EXTRACT_URL).mock(return_value=httpx.Response(200, json=EXTRACT_RESPONSE))
    respx.post(STORE_URL).mock(**store)  # type: ignore[arg-type]

    response = post_pdf(client, name="informe")

    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/problem+json"
    assert "document-store" in response.json()["detail"]


# --- Tamaño del PDF ---


@pytest.fixture
def small_limit() -> Iterator[None]:
    """Baja el límite del PDF a 1 KB para probar el 413 sin mandar 10 MB."""
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, max_pdf_size_bytes=1024
    )
    yield
    app.dependency_overrides.clear()


def test_post_documents_rechaza_un_pdf_mas_grande_que_el_limite_con_413(
    client: TestClient, small_limit: None
) -> None:
    response = client.post(
        "/api/v1/documents",
        files={"file": ("grande.pdf", PDF_CONTENT + b"x" * 2048, "application/pdf")},
    )

    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["title"] == "Content Too Large"
    assert "tamaño máximo" in body["detail"]


@respx.mock
def test_post_documents_reenvia_completo_un_pdf_de_mas_de_1_mb(client: TestClient) -> None:
    content = b"%PDF-1.4\n" + b"x" * (2 * 1024 * 1024)
    extract_route, store_route = mock_services()

    response = post_pdf(client, content=content, name="grande")

    assert response.status_code == 201
    assert content in extract_route.calls.last.request.content
    assert sent_to_store(store_route)["checksum"] == hashlib.sha256(content).hexdigest()


def test_post_documents_solo_con_nombre_devuelve_422(client: TestClient) -> None:
    response = client.post("/api/v1/documents", files={"name": (None, "informe")})

    assert response.status_code == 422
    assert "file" in response.json()["detail"]


def test_post_documents_documenta_el_multipart_en_openapi(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/documents"]["post"]

    form = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert form["required"] == ["file"]
    assert form["properties"]["file"]["format"] == "binary"
    assert "name" in form["properties"]


def test_post_documents_documenta_el_409_en_openapi(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert "409" in schema["paths"]["/api/v1/documents"]["post"]["responses"]
