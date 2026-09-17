import httpx
import pytest
import respx

from app.clients.base_client import BaseClient

BASE_URL = "http://pdf-extractext:8000"


async def test_base_client_configures_base_url_and_timeout() -> None:
    client = BaseClient(base_url=BASE_URL, timeout_seconds=5)

    assert client.base_url == httpx.URL(BASE_URL)
    assert client._client.timeout == httpx.Timeout(5)

    await client.aclose()


@respx.mock
async def test_request_resuelve_el_path_contra_la_base_url() -> None:
    route = respx.get(f"{BASE_URL}/health").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    async with BaseClient(base_url=BASE_URL, timeout_seconds=5) as client:
        response = await client._request("GET", "/health")

    assert route.called
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@respx.mock
async def test_request_reenvia_multipart_y_form_data() -> None:
    route = respx.post(f"{BASE_URL}/api/v1/documents").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )

    async with BaseClient(base_url=BASE_URL, timeout_seconds=5) as client:
        response = await client._request(
            "POST",
            "/api/v1/documents",
            data={"name": "informe"},
            files={"file": ("informe.pdf", b"%PDF-1.4", "application/pdf")},
        )

    assert response.status_code == 201
    sent = route.calls.last.request
    assert sent.headers["content-type"].startswith("multipart/form-data")
    assert b'name="name"' in sent.content
    assert b'filename="informe.pdf"' in sent.content


@respx.mock
async def test_request_propaga_errores_de_transporte() -> None:
    respx.get(f"{BASE_URL}/health").mock(side_effect=httpx.ConnectError)

    async with BaseClient(base_url=BASE_URL, timeout_seconds=5) as client:
        with pytest.raises(httpx.ConnectError):
            await client._request("GET", "/health")


@respx.mock
async def test_request_propaga_timeouts() -> None:
    respx.get(f"{BASE_URL}/health").mock(side_effect=httpx.ReadTimeout)

    async with BaseClient(base_url=BASE_URL, timeout_seconds=5) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client._request("GET", "/health")


async def test_context_manager_cierra_el_cliente() -> None:
    async with BaseClient(base_url=BASE_URL, timeout_seconds=5) as client:
        assert not client._client.is_closed

    assert client._client.is_closed
