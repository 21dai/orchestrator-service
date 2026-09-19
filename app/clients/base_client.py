import logging
from typing import Any, Self

import httpx

from app.clients.circuit_breaker import CircuitBreaker
from app.clients.retry import RetryPolicy

logger = logging.getLogger(__name__)


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

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        retry_policy: RetryPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )
        self._retry_policy = retry_policy
        self._circuit_breaker = circuit_breaker

    @property
    def base_url(self) -> httpx.URL:
        return self._client.base_url

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Envía una request al microservicio y devuelve la respuesta cruda.

        `path` se resuelve contra `base_url`. Aplica la política de Retry si
        hay una configurada; cada intento pasa por el Circuit Breaker (si
        está abierto se corta con `CircuitOpenError`, que no se reintenta).
        Los errores de transporte y los timeouts de httpx se propagan tal
        cual (después de agotar los reintentos): cada cliente concreto decide
        cómo traducirlos a excepciones de dominio.
        """
        if self._retry_policy is None:
            return await self._send(method, path, **kwargs)
        return await self._send_with_retry(self._retry_policy, method, path, **kwargs)

    async def _send_with_retry(
        self, policy: RetryPolicy, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        attempt = 1
        while True:
            try:
                response = await self._send(method, path, **kwargs)
            except Exception as exc:
                if attempt >= policy.max_attempts or not policy.should_retry_exception(exc):
                    raise
                reason = type(exc).__name__
            else:
                if attempt >= policy.max_attempts or not policy.should_retry_response(
                    method, response.status_code
                ):
                    return response
                reason = f"HTTP {response.status_code}"

            delay = policy.delay(attempt)
            logger.warning(
                "Reintentando %s %s (intento %d/%d, motivo: %s, espera: %.2fs)",
                method,
                self.base_url.join(path),
                attempt + 1,
                policy.max_attempts,
                reason,
                delay,
            )
            await policy.sleep(delay)
            attempt += 1

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Un intento real de request, protegido por el Circuit Breaker.

        Para el breaker cuentan como fallo las excepciones de httpx (transporte
        y timeouts) y las respuestas 5xx; un 4xx es una respuesta válida del
        servicio, así que cuenta como éxito. Punto de extensión para el Bulkhead.
        """
        breaker = self._circuit_breaker
        if breaker is None:
            return await self._client.request(method, path, **kwargs)

        breaker.before_request()
        try:
            response = await self._client.request(method, path, **kwargs)
        except Exception:
            breaker.record_failure()
            raise
        if response.is_server_error:
            breaker.record_failure()
        else:
            breaker.record_success()
        return response

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
