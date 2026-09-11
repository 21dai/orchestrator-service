import httpx


class BaseClient:
    """Cliente HTTP base para comunicarse con otro microservicio.

    Un cliente concreto por microservicio extiende esta clase e implementa
    únicamente sus propios endpoints. Los patrones de resiliencia
    (Retry, Circuit Breaker, Bulkhead) se aplicarán sobre esta capa
    sin modificar los services.
    """

    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
