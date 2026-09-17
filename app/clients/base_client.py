from typing import Any, Self

import httpx


class BaseClient:
    """Cliente HTTP base para comunicarse con otro microservicio.

    Un cliente concreto por microservicio extiende esta clase e implementa
    únicamente sus propios endpoints, siempre a través de `_request`: es el
    único punto por donde salen requests del orquestador, así que los
    patrones de resiliencia (Retry, Circuit Breaker, Bulkhead) y la
    observabilidad se aplican ahí sin tocar los services.

    Se instancia una vez por ciclo de vida de la app (no por request) y se
    cierra con `aclose()` o usándolo como context manager asíncrono.
    """

    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    @property
    def base_url(self) -> httpx.URL:
        return self._client.base_url

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Envía una request al microservicio y devuelve la respuesta cruda.

        `path` se resuelve contra `base_url`. Los errores de transporte y los
        timeouts de httpx se propagan tal cual: cada cliente concreto decide
        cómo traducirlos a excepciones de dominio.
        """
        return await self._client.request(method, path, **kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
