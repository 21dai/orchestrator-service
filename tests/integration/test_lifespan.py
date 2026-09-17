from fastapi.testclient import TestClient

from app.dependencies import get_pdf_extract_client
from app.main import app


def test_el_client_de_pdf_extractext_se_cierra_al_apagar_la_app() -> None:
    with TestClient(app):
        pdf_client = get_pdf_extract_client()
        assert not pdf_client._client.is_closed

    assert pdf_client._client.is_closed
    # Un arranque posterior obtiene un client nuevo, no el cerrado.
    assert get_pdf_extract_client() is not pdf_client
