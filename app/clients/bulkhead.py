"""Bulkhead para las llamadas salientes.

Acota cuántas requests hacia un microservicio pueden estar **en vuelo** a la
vez (`max_concurrent`) y cuántas pueden quedar **esperando** un lugar
(`max_waiting`, durante como máximo `acquire_timeout_seconds`). Lo que no
entra se rechaza en el acto con `BulkheadFullError`.

Por qué: cada request al orquestador retiene un PDF completo en memoria
(ADR-0001, hasta 10 MB) mientras espera a pdf-extractext. Según las
mediciones, el hoja procesa ~0,6 uploads grandes por segundo y, saturado,
tarda 30 s en responder o directamente no responde. Sin este límite, un hoja
lento haría que el orquestador acumulara requests (y PDFs) sin tope hasta
agotar memoria o conexiones, y arrastraría también a `/health` y a cualquier
otra operación. Con el Bulkhead, el peor caso por proceso queda acotado a
`max_concurrent + max_waiting` PDFs en memoria y a una espera máxima
conocida; el exceso recibe un 503 inmediato y puede reintentar más tarde.

Es un objeto por microservicio, compartido por todas las requests del
proceso (asyncio single-threaded: no necesita locks).
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class BulkheadFullError(Exception):
    """No hay lugar para la llamada: slots y cola de espera llenos, o se agotó la espera."""


class Bulkhead:
    def __init__(
        self,
        max_concurrent: int = 5,
        max_waiting: int = 10,
        acquire_timeout_seconds: float = 5.0,
        name: str = "servicio",
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent debe ser al menos 1.")
        if max_waiting < 0:
            raise ValueError("max_waiting no puede ser negativo.")
        if acquire_timeout_seconds < 0:
            raise ValueError("acquire_timeout_seconds no puede ser negativo.")
        self.max_concurrent = max_concurrent
        self.max_waiting = max_waiting
        self.acquire_timeout_seconds = acquire_timeout_seconds
        self.name = name

        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active = 0
        self._waiting = 0

    @property
    def active(self) -> int:
        """Requests en vuelo en este momento."""
        return self._active

    @property
    def waiting(self) -> int:
        """Requests esperando un slot en este momento."""
        return self._waiting

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Reserva un slot mientras dura el bloque; lanza `BulkheadFullError` si no hay."""
        await self._acquire()
        try:
            yield
        finally:
            self._active -= 1
            self._semaphore.release()

    async def _acquire(self) -> None:
        if self._semaphore.locked() and self._waiting >= self.max_waiting:
            logger.warning(
                "Bulkhead de %s lleno: %d en vuelo, %d en espera; solicitud rechazada",
                self.name,
                self._active,
                self._waiting,
            )
            raise BulkheadFullError(
                f"Demasiadas solicitudes en curso hacia {self.name} "
                f"({self._active} en vuelo, {self._waiting} en espera)."
            )
        self._waiting += 1
        try:
            async with asyncio.timeout(self.acquire_timeout_seconds):
                await self._semaphore.acquire()
        except TimeoutError as exc:
            logger.warning(
                "Bulkhead de %s: se agotó la espera de %gs por un lugar; solicitud rechazada",
                self.name,
                self.acquire_timeout_seconds,
            )
            raise BulkheadFullError(
                f"Se agotó la espera de {self.acquire_timeout_seconds:g} s "
                f"por un lugar para llamar a {self.name}."
            ) from exc
        finally:
            self._waiting -= 1
        self._active += 1
