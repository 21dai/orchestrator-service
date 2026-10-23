import logging
from dataclasses import dataclass
from typing import Any, NoReturn, Self

import httpx
from pydantic import BaseModel, ValidationError

from app.clients.bulkhead import Bulkhead, BulkheadFullError
from app.clients.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.clients.retry import RetryPolicy
from app.exceptions import OrchestratorError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpstreamErrors:
    """Excepciones de dominio con las que un client traduce los fallos de su servicio.

    Los fallos de red, de resiliencia y los 5xx son iguales para cualquier
    microservicio: `BaseClient` los traduce con estas excepciones y cada
    client concreto resuelve solo lo propio de su contrato (2xx y 4xx).
    """

    service: str
    unavailable: type[OrchestratorError]
    timeout: type[OrchestratorError]
    circuit_open: type[OrchestratorError]
    overloaded: type[OrchestratorError]
    unexpected: type[OrchestratorError]


def retry_after_seconds(response: httpx.Response) -> float | None:
    """Segundos del header `Retry-After`, o None si no viene o no es un número."""
    value = response.headers.get("retry-after", "").strip()
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def error_detail(response: httpx.Response) -> str:
    """Extrae `detail` de un Problem Details; si no hay, usa el cuerpo crudo."""
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict) and isinstance(body.get("detail"), str):
        return str(body["detail"])
    return response.text


def is_load_shedding(response: httpx.Response) -> bool:
    """503 con `Retry-After`: el servicio está sano pero con la cola llena.

    Es como responde pdf-extractext cuando no da abasto (backpressure). No es
    una falla: abrir el circuito por esto cortaría un servicio sano justo
    cuando hay más demanda.
    """
    return (
        response.status_code == httpx.codes.SERVICE_UNAVAILABLE
        and retry_after_seconds(response) is not None
    )


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
        connect_timeout_seconds: float | None = None,
        retry_policy: RetryPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        bulkhead: Bulkhead | None = None,
    ) -> None:
        # El timeout de conexión puede ser más corto que el de lectura: si el
        # servicio está caído conviene fallar rápido, pero un POST grande
        # necesita tiempo para que el hoja responda. (Ojo: `connect=None` en
        # httpx significa "sin límite", no "el default"; por eso el if.)
        timeout = httpx.Timeout(timeout_seconds)
        if connect_timeout_seconds is not None:
            timeout = httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds)
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self._retry_policy = retry_policy
        self._circuit_breaker = circuit_breaker
        self._bulkhead = bulkhead

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
        """Un intento real de request, protegido por Circuit Breaker y Bulkhead.

        Orden: primero el breaker (si el circuito está abierto se rechaza sin
        ocupar un slot ni esperar), después el Bulkhead (acota las requests en
        vuelo y en espera) y recién entonces la red. Para el breaker cuentan
        como fallo las excepciones de httpx (transporte y timeouts) y las
        respuestas 5xx; un 4xx es una respuesta válida del servicio, así que
        cuenta como éxito. No cuentan ni el rechazo del Bulkhead ni un 503 con
        `Retry-After` (cola llena, ver `is_load_shedding`): el hoja no falló.

        Si esta request es la llamada de prueba del half-open y termina sin
        resultado (la rechaza el Bulkhead o se cancela), se libera en el
        `finally` para que el circuito no quede trabado.
        """
        breaker = self._circuit_breaker
        is_trial = breaker.before_request() if breaker is not None else False

        try:
            if self._bulkhead is None:
                return await self._send_tracked(breaker, method, path, **kwargs)
            async with self._bulkhead.slot():
                return await self._send_tracked(breaker, method, path, **kwargs)
        finally:
            if is_trial and breaker is not None:
                breaker.release_trial()

    async def _send_tracked(
        self, breaker: CircuitBreaker | None, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kwargs)
        except Exception:
            if breaker is not None:
                breaker.record_failure()
            raise
        if breaker is not None:
            if is_load_shedding(response):
                pass  # ni éxito ni fallo; si era la prueba, la libera `_send`
            elif response.is_server_error:
                breaker.record_failure()
            else:
                breaker.record_success()
        return response

    async def _call(
        self, errors: UpstreamErrors, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        """`_request` con los fallos de red y de resiliencia ya traducidos.

        Devuelve la respuesta cruda (cualquier código HTTP); lo que no llega
        a ser una respuesta se lanza como excepción de dominio del servicio.
        """
        service = errors.service
        try:
            return await self._request(method, path, **kwargs)
        except BulkheadFullError as exc:
            raise errors.overloaded(
                f"Demasiadas solicitudes en curso hacia {service}; reintentar más tarde."
            ) from exc
        except CircuitOpenError as exc:
            raise errors.circuit_open(
                f"{service} está temporalmente deshabilitado por fallos repetidos; "
                f"reintentar en {exc.retry_after_seconds:.0f} s.",
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc
        except httpx.ConnectTimeout as exc:
            # Es un timeout, pero de conexión: el servicio no está escuchando.
            raise errors.unavailable(f"No se pudo conectar con {service}.") from exc
        except httpx.TimeoutException as exc:
            raise errors.timeout(f"{service} no respondió dentro del timeout configurado.") from exc
        except httpx.TransportError as exc:
            raise errors.unavailable(f"No se pudo conectar con {service}.") from exc

    @staticmethod
    def _raise_unexpected(errors: UpstreamErrors, response: httpx.Response) -> NoReturn:
        """Traduce una respuesta que el contrato no prevé (5xx y otros códigos).

        Un 503 con `Retry-After` es la cola del servicio llena: el cliente puede
        volver a intentar en ese tiempo. Sin `Retry-After`, el servicio no está
        disponible (Traefik sin réplicas sanas, o document-store sin MongoDB).
        """
        detail = error_detail(response)
        if response.status_code == httpx.codes.SERVICE_UNAVAILABLE:
            retry_after = retry_after_seconds(response)
            if retry_after is None:
                raise errors.unavailable(f"{errors.service} no está disponible: {detail}")
            raise errors.overloaded(
                f"{errors.service} está saturado: {detail}", retry_after_seconds=retry_after
            )
        raise errors.unexpected(f"{errors.service} respondió {response.status_code}: {detail}")

    @staticmethod
    def _parse[M: BaseModel](errors: UpstreamErrors, response: httpx.Response, model: type[M]) -> M:
        """Valida el body de una respuesta exitosa contra el modelo del contrato."""
        try:
            return model.model_validate_json(response.content)
        except ValidationError as exc:
            raise errors.unexpected(
                f"La respuesta de {errors.service} no tiene el formato esperado."
            ) from exc

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
