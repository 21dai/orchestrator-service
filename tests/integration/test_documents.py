from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_post_documents_recibe_pdf_y_devuelve_metadatos() -> None:
    content = b"%PDF-1.4 contenido de prueba"

    response = client.post(
        "/api/v1/documents",
        files={"file": ("informe.pdf", content, "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "filename": "informe.pdf",
        "content_type": "application/pdf",
        "size_bytes": len(content),
    }


def test_post_documents_sin_archivo_devuelve_422() -> None:
    response = client.post("/api/v1/documents")

    assert response.status_code == 422
