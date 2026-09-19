"""Política de reintentos para las llamadas salientes (patrón Retry).

Qué se reintenta y qué no sale de las mediciones de carga sobre pdf-extractext
(CONTEXTO, 2026-09-16): un POST que agota el timeout **igual crea el
documento** en el hoja y deja el proceso ocupado; reintentarlo empeora la cola
y termina en 400 por checksum duplicado. Por eso la regla es conservadora:

- Se reintenta solo lo que seguro no llegó al servidor: fallos al conectar
  (`ConnectError`, `ConnectTimeout`).
- Un timeout de lectura/escritura o una conexión cortada a mitad de camino
  NO se reintentan: la request ya salió y puede haberse procesado.
- Un 5xx se reintenta solo en métodos idempotentes (GET, HEAD, PUT, DELETE,
  OPTIONS); en POST podría duplicar trabajo.
- Los 4xx nunca: son errores del pedido, no del servicio.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS"})

RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
)

SleepFn = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryPolicy:
    """Cantidad máxima de intentos y espera exponencial con *full jitter*.

    `max_attempts` cuenta el intento original: 3 significa una llamada y hasta
    dos reintentos. La espera antes del reintento `n` se sortea uniformemente
    en `[0, min(backoff * 2**(n-1), max_backoff)]`, para que varias réplicas
    no reintenten todas al mismo tiempo contra un servicio que se está
    recuperando.
    """

    max_attempts: int = 3
    backoff_seconds: float = 0.2
    max_backoff_seconds: float = 2.0
    sleep: SleepFn = field(default=asyncio.sleep, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts debe ser al menos 1.")
        if self.backoff_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("Los tiempos de espera no pueden ser negativos.")

    def should_retry_exception(self, exc: Exception) -> bool:
        return isinstance(exc, RETRYABLE_EXCEPTIONS)

    def should_retry_response(self, method: str, status_code: int) -> bool:
        return status_code >= 500 and method.upper() in IDEMPOTENT_METHODS

    def delay(self, attempt: int) -> float:
        """Segundos a esperar antes del reintento número `attempt` (desde 1)."""
        cap = min(self.backoff_seconds * 2 ** (attempt - 1), self.max_backoff_seconds)
        return random.uniform(0, cap) if cap > 0 else 0.0
