"""CRUD de documentos (listar, ver, renombrar y borrar) con document-store simulado por respx."""

from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import get_settings

STORE_URL = f"{get_settings().document_store_base_url}/api/v1/documents"
DOCUMENT_URL = f"{STORE_URL}/7"

STORED = {
    "id": 7,
    "name": "informe",
    "original_filename": "informe.pdf",
    "checksum": "a" * 64,
    "file_size": 1024,
    "page_count": 3,
    "extracted_text": "# Informe",
    "is_processed": True,
    "created_at": "2026-10-23T10:00:00Z",
    "updated_at": "2026-10-23T10:00:00Z",
}

#: Cómo responde el orquestador el documento STORED (sin exponer el modelo de document-store).
PUBLIC = {
    "document_id": 7,
    "name": "informe",
    "filename": "informe.pdf",
    "size_bytes": 1024,
    "checksum": "a" * 64,
    "page_count": 3,
    "is_processed": True,
    "extracted_text": "# Informe",
    "processed_at": "2026-10-23T10:00:00Z",
    "updated_at": "2026-10-23T10:00:00Z",
}


def problem(status: int, detail: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"type": "about:blank", "title": "Error", "status": status, "detail": detail},
        headers={"content-type": "application/problem+json"},
    )


def assert_problem(response: httpx.Response, status: int) -> dict[str, object]:
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == status
    assert set(body) == {"type", "title", "status", "detail", "instance"}
    return body


# --- Camino feliz --------------------------------------------------------------


@respx.mock
def test_get_lista_devuelve_la_pagina_con_skip_y_limit(client: TestClient) -> None:
    route = respx.get(STORE_URL).mock(
        return_value=httpx.Response(200, json=[STORED, {**STORED, "id": 8}])
    )

    response = client.get("/api/v1/documents", params={"skip": 5, "limit": 2})

    assert response.status_code == 200
    assert response.json() == [PUBLIC, {**PUBLIC, "document_id": 8}]
    assert dict(route.calls.last.request.url.params) == {"skip": "5", "limit": "2"}


@respx.mock
def test_get_lista_usa_skip_0_y_limit_10_por_defecto(client: TestClient) -> None:
    route = respx.get(STORE_URL).mock(return_value=httpx.Response(200, json=[]))

    response = client.get("/api/v1/documents")

    assert response.status_code == 200
    assert response.json() == []
    assert dict(route.calls.last.request.url.params) == {"skip": "0", "limit": "10"}


@respx.mock
def test_get_por_id_devuelve_el_documento(client: TestClient) -> None:
    respx.get(DOCUMENT_URL).mock(return_value=httpx.Response(200, json=STORED))

    response = client.get("/api/v1/documents/7")

    assert response.status_code == 200
    assert response.json() == PUBLIC


@respx.mock
def test_put_renombra_y_devuelve_el_documento_con_updated_at(client: TestClient) -> None:
    renamed = {**STORED, "name": "nuevo nombre", "updated_at": "2026-11-09T15:30:00Z"}
    route = respx.put(DOCUMENT_URL).mock(return_value=httpx.Response(200, json=renamed))

    response = client.put("/api/v1/documents/7", json={"name": "  nuevo nombre  "})

    assert response.status_code == 200
    assert response.json() == {
        **PUBLIC,
        "name": "nuevo nombre",
        "updated_at": "2026-11-09T15:30:00Z",
    }
    # El nombre llega a document-store ya sin los espacios de los extremos.
    assert route.calls.last.request.content == b'{"name":"nuevo nombre"}'


@respx.mock
def test_delete_responde_204_sin_cuerpo(client: TestClient) -> None:
    route = respx.delete(DOCUMENT_URL).mock(return_value=httpx.Response(204))

    response = client.delete("/api/v1/documents/7")

    assert response.status_code == 204
    assert response.content == b""
    assert route.called


# --- 404 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [("GET", {}), ("PUT", {"json": {"name": "x"}}), ("DELETE", {})],
    ids=["ver", "renombrar", "borrar"],
)
@respx.mock
def test_un_id_inexistente_responde_404_con_problem_details(
    client: TestClient, method: str, kwargs: dict[str, Any]
) -> None:
    respx.route(method=method, url=f"{STORE_URL}/99").mock(
        return_value=problem(404, "No existe un documento con id 99.")
    )

    response = client.request(method, "/api/v1/documents/99", **kwargs)

    body = assert_problem(response, 404)
    assert body["detail"] == "No existe un documento con id 99."
    assert str(body["instance"]).endswith("/api/v1/documents/99")


# --- 422: se valida en el orquestador, sin llamar a document-store -------------


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("GET", "/api/v1/documents?skip=-1", {}),
        ("GET", "/api/v1/documents?limit=0", {}),
        ("GET", "/api/v1/documents?limit=101", {}),
        ("GET", "/api/v1/documents?limit=abc", {}),
        ("GET", "/api/v1/documents/0", {}),
        ("GET", "/api/v1/documents/abc", {}),
        ("PUT", "/api/v1/documents/-5", {"json": {"name": "x"}}),
        ("DELETE", "/api/v1/documents/0", {}),
        ("PUT", "/api/v1/documents/7", {"json": {"name": "   "}}),
        ("PUT", "/api/v1/documents/7", {"json": {"name": "x" * 256}}),
        ("PUT", "/api/v1/documents/7", {"json": {"name": 123}}),
        ("PUT", "/api/v1/documents/7", {"json": {"name": "x", "checksum": "a" * 64}}),
        ("PUT", "/api/v1/documents/7", {"json": {}}),
        ("PUT", "/api/v1/documents/7", {}),
    ],
    ids=[
        "skip-negativo",
        "limit-0",
        "limit-101",
        "limit-no-numerico",
        "ver-id-0",
        "ver-id-no-numerico",
        "renombrar-id-negativo",
        "borrar-id-0",
        "nombre-solo-espacios",
        "nombre-de-256",
        "nombre-numero",
        "campo-extra",
        "sin-name",
        "sin-body",
    ],
)
@respx.mock
def test_un_pedido_invalido_responde_422_sin_llamar_a_document_store(
    client: TestClient, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    store = respx.route(url__startswith=STORE_URL).mock(return_value=httpx.Response(500))

    response = client.request(method, path, **kwargs)

    assert_problem(response, 422)
    assert not store.called


# --- 503 / 504: document-store no responde -------------------------------------

OPERATIONS = [
    ("GET", STORE_URL, "/api/v1/documents", {}),
    ("GET", DOCUMENT_URL, "/api/v1/documents/7", {}),
    ("PUT", DOCUMENT_URL, "/api/v1/documents/7", {"json": {"name": "x"}}),
    ("DELETE", DOCUMENT_URL, "/api/v1/documents/7", {}),
]
OPERATION_IDS = ["listar", "ver", "renombrar", "borrar"]


@pytest.mark.parametrize(("method", "store_url", "path", "kwargs"), OPERATIONS, ids=OPERATION_IDS)
@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("refused"), problem(503, "La base de datos no está disponible.")],
    ids=["conexion-rechazada", "sin-mongodb"],
)
@respx.mock
def test_document_store_caido_responde_503(
    client: TestClient,
    method: str,
    store_url: str,
    path: str,
    kwargs: dict[str, Any],
    failure: Exception | httpx.Response,
) -> None:
    route = respx.route(method=method, url=store_url)
    if isinstance(failure, httpx.Response):
        route.mock(return_value=failure)
    else:
        route.mock(side_effect=failure)

    response = client.request(method, path, **kwargs)

    body = assert_problem(response, 503)
    assert "document-store" in str(body["detail"])


@pytest.mark.parametrize(("method", "store_url", "path", "kwargs"), OPERATIONS, ids=OPERATION_IDS)
@respx.mock
def test_document_store_sin_responder_a_tiempo_responde_504(
    client: TestClient, method: str, store_url: str, path: str, kwargs: dict[str, Any]
) -> None:
    respx.route(method=method, url=store_url).mock(side_effect=httpx.ReadTimeout("timeout"))

    response = client.request(method, path, **kwargs)

    body = assert_problem(response, 504)
    assert "document-store" in str(body["detail"])


# --- /docs ---------------------------------------------------------------------


def test_los_endpoints_del_crud_aparecen_en_openapi_con_sus_errores(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    collection = paths["/api/v1/documents"]
    item = paths["/api/v1/documents/{document_id}"]
    assert {"get", "post"} <= set(collection)
    assert {"get", "put", "delete"} <= set(item)
    assert {"200", "422", "502", "503", "504"} <= set(collection["get"]["responses"])
    for method, ok in [("get", "200"), ("put", "200"), ("delete", "204")]:
        assert {ok, "404", "422", "502", "503", "504"} <= set(item[method]["responses"]), method
        error = item[method]["responses"]["404"]["content"]
        assert "application/problem+json" in error
