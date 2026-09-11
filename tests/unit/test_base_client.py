import httpx

from app.clients.base_client import BaseClient


async def test_base_client_configures_base_url_and_timeout() -> None:
    client = BaseClient(base_url="http://service-a:8001", timeout_seconds=5)

    assert client._client.base_url == httpx.URL("http://service-a:8001")
    assert client._client.timeout == httpx.Timeout(5)

    await client.aclose()
