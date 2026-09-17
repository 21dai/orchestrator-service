from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PDF_CONTENT = b"%PDF-1.4\n%contenido de prueba"


def test_post_documents_recibe_pdf_y_devuelve_metadatos() -> None:
    response = client.post(
        "/api/v1/documents",
        files={"file": ("informe.pdf", PDF_CONTENT, "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "filename": "informe.pdf",
        "content_type": "application/pdf",
        "size_bytes": len(PDF_CONTENT),
    }


def test_post_documents_sin_archivo_devuelve_422() -> None:
    response = client.post("/api/v1/documents")

    assert response.status_code == 422


def test_post_documents_rechaza_archivo_no_pdf_con_problem_details() -> None:
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


def test_post_documents_rechaza_pdf_con_contenido_invalido() -> None:
    response = client.post(
        "/api/v1/documents",
        files={"file": ("falso.pdf", b"no soy un pdf", "application/pdf")},
    )

    assert response.status_code == 400
    assert "no corresponde a un PDF" in response.json()["detail"]
