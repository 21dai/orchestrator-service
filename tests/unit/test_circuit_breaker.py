import pytest

from app.clients.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def breaker(clock: FakeClock) -> CircuitBreaker:
    return CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=30, clock=clock)


def fail_n_times(breaker: CircuitBreaker, n: int) -> None:
    for _ in range(n):
        breaker.before_request()
        breaker.record_failure()


def test_arranca_cerrado_y_deja_pasar(breaker: CircuitBreaker) -> None:
    assert breaker.state is CircuitState.CLOSED
    breaker.before_request()  # no lanza


def test_sigue_cerrado_por_debajo_del_umbral(breaker: CircuitBreaker) -> None:
    fail_n_times(breaker, 2)

    assert breaker.state is CircuitState.CLOSED
    breaker.before_request()


def test_se_abre_al_alcanzar_el_umbral_de_fallos_consecutivos(breaker: CircuitBreaker) -> None:
    fail_n_times(breaker, 3)

    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_request()


def test_un_exito_reinicia_la_cuenta_de_fallos(breaker: CircuitBreaker) -> None:
    fail_n_times(breaker, 2)
    breaker.before_request()
    breaker.record_success()
    fail_n_times(breaker, 2)

    assert breaker.state is CircuitState.CLOSED


def test_pasa_a_half_open_cuando_vence_el_tiempo_de_recuperacion(
    breaker: CircuitBreaker, clock: FakeClock
) -> None:
    fail_n_times(breaker, 3)

    clock.advance(29.9)
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_request()

    clock.advance(0.2)
    assert breaker.state is CircuitState.HALF_OPEN
    breaker.before_request()  # deja pasar la llamada de prueba


def test_en_half_open_solo_deja_pasar_una_llamada_de_prueba(
    breaker: CircuitBreaker, clock: FakeClock
) -> None:
    fail_n_times(breaker, 3)
    clock.advance(30)

    breaker.before_request()
    with pytest.raises(CircuitOpenError):
        breaker.before_request()


def test_exito_en_half_open_cierra_el_circuito(breaker: CircuitBreaker, clock: FakeClock) -> None:
    fail_n_times(breaker, 3)
    clock.advance(30)

    breaker.before_request()
    breaker.record_success()

    assert breaker.state is CircuitState.CLOSED
    breaker.before_request()
    breaker.before_request()  # ya no está limitado a una sola llamada


def test_fallo_en_half_open_vuelve_a_abrir_y_reinicia_el_tiempo(
    breaker: CircuitBreaker, clock: FakeClock
) -> None:
    fail_n_times(breaker, 3)
    clock.advance(30)

    breaker.before_request()
    breaker.record_failure()

    assert breaker.state is CircuitState.OPEN
    clock.advance(29)
    with pytest.raises(CircuitOpenError):
        breaker.before_request()
    clock.advance(1)
    assert breaker.state is CircuitState.HALF_OPEN


def test_circuit_open_error_informa_cuanto_falta_para_reintentar(
    breaker: CircuitBreaker, clock: FakeClock
) -> None:
    fail_n_times(breaker, 3)
    clock.advance(10)

    with pytest.raises(CircuitOpenError) as exc_info:
        breaker.before_request()

    assert exc_info.value.retry_after_seconds == pytest.approx(20)


@pytest.mark.parametrize(
    ("threshold", "timeout"),
    [(0, 30), (-1, 30), (3, -1)],
)
def test_valida_los_parametros(threshold: int, timeout: float) -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=threshold, recovery_timeout_seconds=timeout)
