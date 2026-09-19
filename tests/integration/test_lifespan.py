from fastapi.testclient import TestClient

from app.dependencies import get_pdf_extract_client
from app.main import app


def test_el_client_de_pdf_extractext_se_crea_al_arrancar_la_app() -> None:
    """Regresión: si el client se creara perezosamente en la primera request,
    varias requests simultáneas construirían cada una el suyo (lru_cache no es
    atómico entre hilos) y Circuit Breaker y Bulkhead no compartirían estado."""
    get_pdf_extract_client.cache_clear()

    with TestClient(app):
        assert get_pdf_extract_client.cache_info().currsize == 1


def test_el_client_de_pdf_extractext_se_cierra_al_apagar_la_app() -> None:
    with TestClient(app):
        pdf_client = get_pdf_extract_client()
        assert not pdf_client._client.is_closed

    assert pdf_client._client.is_closed
    # Un arranque posterior obtiene un client nuevo, no el cerrado.
    with TestClient(app):
        assert get_pdf_extract_client() is not pdf_client
