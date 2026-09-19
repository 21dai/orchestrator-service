"""Circuit Breaker para las llamadas salientes.

Evita seguir llamando a un microservicio que está fallando repetidamente:
cuando se acumulan `failure_threshold` fallos consecutivos el circuito se
abre y las llamadas se rechazan de inmediato (sin tocar la red) durante
`recovery_timeout_seconds`. Pasado ese tiempo se deja pasar **una** llamada
de prueba (half-open): si sale bien el circuito se cierra; si falla, se
vuelve a abrir y el reloj arranca de nuevo.

    CLOSED ──(N fallos seguidos)──▶ OPEN ──(vence el timeout)──▶ HALF_OPEN
      ▲                                ▲                            │
      └────────(éxito)─────────────────┴─────────(fallo)────────────┘

Qué cuenta como fallo lo decide quien lo usa (`BaseClient`): errores de
transporte, timeouts y respuestas 5xx. Según las mediciones sobre
pdf-extractext, los timeouts consecutivos son la señal principal de que el
hoja está saturado, y seguir enviándole PDFs solo agranda su cola.

Es un objeto por microservicio, compartido por todas las requests del
proceso. No necesita locks: asyncio es single-threaded y no hay `await`
entre leer y escribir el estado.
"""

import logging
import time
from collections.abc import Callable
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """La llamada se rechazó porque el circuito está abierto."""

    def __init__(self, name: str, retry_after_seconds: float) -> None:
        self.name = name
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Circuito abierto para {name}: reintentar en {retry_after_seconds:.0f} s."
        )


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
        name: str = "servicio",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold debe ser al menos 1.")
        if recovery_timeout_seconds < 0:
            raise ValueError("recovery_timeout_seconds no puede ser negativo.")
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.name = name
        self._clock = clock

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._trial_in_flight = False

    @property
    def state(self) -> CircuitState:
        if self._state is CircuitState.OPEN and self._remaining() <= 0:
            self._transition(CircuitState.HALF_OPEN)
        return self._state

    def before_request(self) -> None:
        """Llamar antes de cada request. Lanza `CircuitOpenError` si no debe salir."""
        state = self.state
        if state is CircuitState.OPEN:
            raise CircuitOpenError(self.name, self._remaining())
        if state is CircuitState.HALF_OPEN:
            if self._trial_in_flight:
                raise CircuitOpenError(self.name, 0.0)
            self._trial_in_flight = True

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._trial_in_flight = False
        if self._state is not CircuitState.CLOSED:
            self._transition(CircuitState.CLOSED)

    def record_failure(self) -> None:
        self._trial_in_flight = False
        self._consecutive_failures += 1
        if self._state is CircuitState.HALF_OPEN or (
            self._consecutive_failures >= self.failure_threshold
        ):
            self._opened_at = self._clock()
            self._transition(CircuitState.OPEN)

    def _remaining(self) -> float:
        return self.recovery_timeout_seconds - (self._clock() - self._opened_at)

    def _transition(self, new_state: CircuitState) -> None:
        if new_state is self._state:
            return
        logger.warning(
            "Circuit breaker de %s: %s -> %s (fallos consecutivos: %d)",
            self.name,
            self._state.value,
            new_state.value,
            self._consecutive_failures,
        )
        self._state = new_state
        if new_state is CircuitState.HALF_OPEN:
            self._trial_in_flight = False
