"""CRUD de documentos punta a punta contra el stack desplegado.

Cliente → Traefik → orchestrator → document-store → MongoDB: subir un PDF,
encontrarlo en la lista, verlo, renombrarlo, borrarlo y que después dé 404.
document-store no se expone por Traefik: todo pasa por el orquestador.
"""

import os
from datetime import datetime

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SYSTEM_TESTS") != "1",
    reason="Tests de sistema: requieren el stack desplegado (RUN_SYSTEM_TESTS=1).",
)

DOCUMENTS = "/api/v1/documents"
PAGE_SIZE = 100


def list_all_ids(http: httpx.Client) -> list[int]:
    """Recorre todas las páginas de la lista (la base persiste entre corridas)."""
    ids: list[int] = []
    skip = 0
    while True:
        response = http.get(DOCUMENTS, params={"skip": skip, "limit": PAGE_SIZE})
        assert response.status_code == 200, response.text
        page = response.json()
        ids += [document["document_id"] for document in page]
        if len(page) < PAGE_SIZE:
            return ids
        skip += PAGE_SIZE


def test_subir_listar_ver_renombrar_borrar_y_despues_404(
    http: httpx.Client, pdf_file: tuple[str, bytes]
) -> None:
    filename, content = pdf_file

    # Subir.
    created = http.post(
        DOCUMENTS,
        data={"name": "crud-sistema"},
        files={"file": (filename, content, "application/pdf")},
    )
    assert created.status_code == 201, created.text
    document = created.json()
    document_id = document["document_id"]
    path = f"{DOCUMENTS}/{document_id}"

    # Listar: aparece, y la lista está ordenada por id.
    ids = list_all_ids(http)
    assert document_id in ids
    assert ids == sorted(ids)

    # Ver: el mismo documento que devolvió el alta.
    fetched = http.get(path)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == document

    # Renombrar: cambia el nombre y updated_at; el resto queda igual.
    renamed = http.put(path, json={"name": "  crud-sistema renombrado  "})
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body["name"] == "crud-sistema renombrado"
    assert datetime.fromisoformat(body["updated_at"]) >= datetime.fromisoformat(
        document["updated_at"]
    )
    unchanged = {k: v for k, v in body.items() if k not in {"name", "updated_at"}}
    assert unchanged == {k: v for k, v in document.items() if k not in {"name", "updated_at"}}
    assert http.get(path).json()["name"] == "crud-sistema renombrado"

    # Borrar: 204 sin cuerpo.
    deleted = http.delete(path)
    assert deleted.status_code == 204, deleted.text
    assert deleted.content == b""

    # Ya no existe: 404 con Problem Details, y tampoco se puede borrar dos veces.
    for response in (http.get(path), http.delete(path)):
        assert response.status_code == 404, response.text
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["status"] == 404
    assert document_id not in list_all_ids(http)


def test_renombrar_con_un_nombre_invalido_responde_422_y_no_cambia_nada(
    http: httpx.Client, pdf_file: tuple[str, bytes]
) -> None:
    filename, content = pdf_file
    created = http.post(DOCUMENTS, files={"file": (filename, content, "application/pdf")})
    assert created.status_code == 201, created.text
    document = created.json()
    path = f"{DOCUMENTS}/{document['document_id']}"

    try:
        response = http.put(path, json={"name": "   "})

        assert response.status_code == 422, response.text
        assert response.headers["content-type"] == "application/problem+json"
        assert http.get(path).json() == document
    finally:
        http.delete(path)
