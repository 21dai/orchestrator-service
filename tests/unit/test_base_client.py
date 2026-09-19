import httpx
import pytest
import respx

from app.clients.base_client import BaseClient
from app.clients.retry import RetryPolicy

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


# --- Retry -------------------------------------------------------------------

NO_BACKOFF = RetryPolicy(max_attempts=3, backoff_seconds=0)


def connect_error() -> httpx.ConnectError:
    return httpx.ConnectError("connection refused")


@respx.mock
async def test_request_reintenta_fallos_de_conexion_hasta_tener_exito() -> None:
    route = respx.get(f"{BASE_URL}/health").mock(
        side_effect=[connect_error(), connect_error(), httpx.Response(200)]
    )

    async with BaseClient(BASE_URL, timeout_seconds=5, retry_policy=NO_BACKOFF) as client:
        response = await client._request("GET", "/health")

    assert response.status_code == 200
    assert route.call_count == 3


@respx.mock
async def test_request_agota_los_intentos_y_propaga_el_ultimo_error() -> None:
    route = respx.get(f"{BASE_URL}/health").mock(side_effect=connect_error())

    async with BaseClient(BASE_URL, timeout_seconds=5, retry_policy=NO_BACKOFF) as client:
        with pytest.raises(httpx.ConnectError):
            await client._request("GET", "/health")

    assert route.call_count == 3


@respx.mock
async def test_request_no_reintenta_un_timeout_de_lectura() -> None:
    route = respx.post(f"{BASE_URL}/api/v1/documents").mock(
        side_effect=httpx.ReadTimeout("timeout")
    )

    async with BaseClient(BASE_URL, timeout_seconds=5, retry_policy=NO_BACKOFF) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client._request("POST", "/api/v1/documents")

    assert route.call_count == 1


@respx.mock
async def test_request_reintenta_5xx_solo_en_metodos_idempotentes() -> None:
    get_route = respx.get(f"{BASE_URL}/health").mock(
        side_effect=[httpx.Response(503), httpx.Response(200)]
    )
    post_route = respx.post(f"{BASE_URL}/api/v1/documents").mock(return_value=httpx.Response(503))

    async with BaseClient(BASE_URL, timeout_seconds=5, retry_policy=NO_BACKOFF) as client:
        get_response = await client._request("GET", "/health")
        post_response = await client._request("POST", "/api/v1/documents")

    assert get_response.status_code == 200
    assert get_route.call_count == 2
    assert post_response.status_code == 503
    assert post_route.call_count == 1


@respx.mock
async def test_request_devuelve_el_ultimo_5xx_si_se_agotan_los_intentos() -> None:
    route = respx.get(f"{BASE_URL}/health").mock(return_value=httpx.Response(503))

    async with BaseClient(BASE_URL, timeout_seconds=5, retry_policy=NO_BACKOFF) as client:
        response = await client._request("GET", "/health")

    assert response.status_code == 503
    assert route.call_count == 3


@respx.mock
async def test_request_no_reintenta_errores_4xx() -> None:
    route = respx.get(f"{BASE_URL}/x").mock(return_value=httpx.Response(404))

    async with BaseClient(BASE_URL, timeout_seconds=5, retry_policy=NO_BACKOFF) as client:
        response = await client._request("GET", "/x")

    assert response.status_code == 404
    assert route.call_count == 1


@respx.mock
async def test_request_espera_entre_reintentos_segun_la_politica() -> None:
    respx.get(f"{BASE_URL}/health").mock(
        side_effect=[connect_error(), connect_error(), httpx.Response(200)]
    )
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    policy = RetryPolicy(
        max_attempts=3, backoff_seconds=0.5, max_backoff_seconds=0.5, sleep=fake_sleep
    )
    async with BaseClient(BASE_URL, timeout_seconds=5, retry_policy=policy) as client:
        await client._request("GET", "/health")

    assert len(waits) == 2
    assert all(0 <= w <= 0.5 for w in waits)


@respx.mock
async def test_request_sin_politica_no_reintenta() -> None:
    route = respx.get(f"{BASE_URL}/health").mock(side_effect=connect_error())

    async with BaseClient(BASE_URL, timeout_seconds=5, retry_policy=None) as client:
        with pytest.raises(httpx.ConnectError):
            await client._request("GET", "/health")

    assert route.call_count == 1
