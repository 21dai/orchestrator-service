import httpx
import pytest

from app.clients.retry import RetryPolicy


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        # La conexión nunca se estableció: la request no llegó, reintentar es seguro.
        (httpx.ConnectError("refused"), True),
        (httpx.ConnectTimeout("timeout"), True),
        # La request ya salió: el servidor puede estar procesándola (mediciones:
        # un POST que agota el timeout igual crea el documento). No reintentar.
        (httpx.ReadTimeout("timeout"), False),
        (httpx.WriteTimeout("timeout"), False),
        (httpx.PoolTimeout("timeout"), False),
        (httpx.RemoteProtocolError("cortado"), False),
        (ValueError("otra cosa"), False),
    ],
)
def test_should_retry_exception(exc: Exception, expected: bool) -> None:
    assert RetryPolicy().should_retry_exception(exc) is expected


@pytest.mark.parametrize(
    ("method", "status", "expected"),
    [
        # 5xx en métodos idempotentes: reintentar.
        ("GET", 500, True),
        ("GET", 502, True),
        ("GET", 503, True),
        ("GET", 504, True),
        ("HEAD", 503, True),
        ("DELETE", 503, True),
        ("PUT", 503, True),
        # POST no es idempotente: un 5xx puede haber procesado la request.
        ("POST", 503, False),
        ("POST", 500, False),
        # Errores del cliente y éxitos: nunca.
        ("GET", 400, False),
        ("GET", 404, False),
        ("GET", 200, False),
        ("POST", 201, False),
    ],
)
def test_should_retry_response(method: str, status: int, expected: bool) -> None:
    assert RetryPolicy().should_retry_response(method, status) is expected


def test_max_attempts_debe_ser_al_menos_uno() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=0)


def test_delay_crece_exponencialmente_con_jitter_y_tope() -> None:
    policy = RetryPolicy(backoff_seconds=0.2, max_backoff_seconds=1.0)

    for attempt, cap in [(1, 0.2), (2, 0.4), (3, 0.8), (4, 1.0), (10, 1.0)]:
        delays = [policy.delay(attempt) for _ in range(50)]
        assert all(0 <= d <= cap for d in delays), (attempt, delays)


def test_delay_es_cero_sin_backoff() -> None:
    assert RetryPolicy(backoff_seconds=0).delay(3) == 0
