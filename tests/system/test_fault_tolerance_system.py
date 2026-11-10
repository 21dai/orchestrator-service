"""Fallas de persistencia contra el stack real: se detiene document-store.

El orquestador llama a document-store a través del entrypoint interno de
Traefik, que tiene el circuit breaker (`cb-document-store`, ADR-0005): con
document-store caído, Traefik abre el circuito y responde 503 al instante.

Doble opt-in: además de `RUN_SYSTEM_TESTS=1` exigen `RUN_FAULT_TESTS=1`,
porque detienen contenedores del stack de `../infrastructure` (ver
`INFRASTRUCTURE_DIR` en conftest.py). El fixture siempre vuelve a levantar el
servicio y espera a que esté `healthy`, aunque el test falle.

Lo que no se puede provocar en el stack real sin tocar código queda cubierto
por los tests de integración con respx (`tests/integration/test_documents.py`,
`test_post_documents_informa_las_fallas_de_document_store`):

- document-store responde 500 → `502` (caso `5xx`);
- document-store sin MongoDB, 503 → `503` (caso `sin-base`);
- timeout de document-store → `504` (caso `timeout`).
"""

import os
import time
from collections.abc import Callable, Iterator

import httpx
import pytest

from tests.system.conftest import DockerCompose

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_SYSTEM_TESTS") != "1",
        reason="Tests de sistema: requieren el stack desplegado (RUN_SYSTEM_TESTS=1).",
    ),
    pytest.mark.skipif(
        os.environ.get("RUN_FAULT_TESTS") != "1",
        reason="Detienen contenedores del stack: requieren además RUN_FAULT_TESTS=1.",
    ),
]

DOCUMENTS = "/api/v1/documents"
DOCUMENT_STORE = "document-store"

# fallbackduration + recoveryduration del middleware cb-document-store de
# Traefik (30 s + 10 s en ../infrastructure/docker-compose.yml): lo máximo que
# el circuito puede seguir abierto o dejando pasar solo parte del tráfico.
TRAEFIK_CIRCUIT_SECONDS = float(os.environ.get("TRAEFIK_CIRCUIT_SECONDS", "40"))
# Con el circuito abierto Traefik responde sin llamar a document-store: una
# respuesta en menos de este tiempo muestra que el circuito está abierto (una
# conexión fallida a document-store tarda varios segundos).
CIRCUIT_OPEN_MAX_SECONDS = 1.0
# Requests que alcanzan para abrir el circuito: con document-store caído
# fallan todas (más de 30 % de errores de red).
REQUESTS_TO_OPEN = 5
# Margen para que document-store reconecte con MongoDB y Traefik/Docker DNS
# vuelvan a resolverlo.
RECOVERY_MARGIN_SECONDS = 30


def post_pdf(http: httpx.Client, pdf: tuple[str, bytes]) -> httpx.Response:
    filename, content = pdf
    return http.post(
        DOCUMENTS, data={"name": "falla"}, files={"file": (filename, content, "application/pdf")}
    )


def post_until_not_503(
    http: httpx.Client, pdf: tuple[str, bytes], timeout: float
) -> httpx.Response:
    """Reintenta mientras el orquestador responda 503, hasta `timeout` segundos.

    Por qué no alcanza con esperar a que document-store esté `healthy`: si
    durante la caída se abrió el circuito de Traefik, responde 503 sin llamar
    a nadie durante `fallbackduration` y después deja pasar solo parte del
    tráfico durante `recoveryduration`. En lugar de un `sleep` fijo de ese
    tiempo (lento cuando el circuito nunca se abrió), se reintenta cada
    segundo y se corta en cuanto responde otra cosa.
    """
    deadline = time.monotonic() + timeout
    while True:
        response = post_pdf(http, pdf)
        if response.status_code != 503 or time.monotonic() >= deadline:
            return response
        time.sleep(1)


@pytest.fixture
def document_store_down(compose: DockerCompose) -> Iterator[Callable[[], None]]:
    """Detiene document-store y devuelve la función que lo restaura.

    El `finally` lo vuelve a levantar y espera `healthy` aunque el test falle
    antes de restaurarlo él mismo (`docker compose start` sobre un servicio
    que ya corre no hace nada).
    """

    def restore() -> None:
        compose.start(DOCUMENT_STORE)
        compose.wait_healthy(DOCUMENT_STORE)

    compose.stop(DOCUMENT_STORE)
    try:
        yield restore
    finally:
        restore()


def test_document_store_caido_responde_503_y_al_volver_se_guarda_el_mismo_pdf(
    http: httpx.Client,
    pdf_file: tuple[str, bytes],
    document_store_down: Callable[[], None],
) -> None:
    caido = post_pdf(http, pdf_file)

    # Traefik no llega a document-store (502) y el orquestador lo informa
    # como 503 Problem Details que nombra al servicio caído.
    assert caido.status_code == 503, caido.text
    assert caido.headers["content-type"] == "application/problem+json"
    body = caido.json()
    assert body["status"] == 503
    assert DOCUMENT_STORE in body["detail"]

    document_store_down()

    # El mismo PDF se guarda: si el intento fallido hubiera dejado algo a
    # medias con ese checksum, document-store respondería 409.
    response = post_until_not_503(
        http, pdf_file, timeout=TRAEFIK_CIRCUIT_SECONDS + RECOVERY_MARGIN_SECONDS
    )
    assert response.status_code == 201, response.text


def test_con_document_store_caido_traefik_abre_el_circuito_y_corta_al_instante(
    http: httpx.Client,
    pdf_file: tuple[str, bytes],
    document_store_down: Callable[[], None],
) -> None:
    elapsed: list[float] = []
    for _ in range(REQUESTS_TO_OPEN):
        start = time.monotonic()
        response = post_pdf(http, pdf_file)
        elapsed.append(time.monotonic() - start)
        assert response.status_code == 503, response.text
        assert response.headers["content-type"] == "application/problem+json"
        # Traefik no manda Retry-After con el circuito abierto.
        assert "retry-after" not in response.headers

    # Las primeras esperan a que falle la conexión; con el circuito abierto,
    # Traefik corta sin llamar a document-store.
    assert elapsed[-1] < CIRCUIT_OPEN_MAX_SECONDS, elapsed

    document_store_down()

    # Pasados fallbackduration + recoveryduration el circuito se cierra.
    response = post_until_not_503(
        http, pdf_file, timeout=TRAEFIK_CIRCUIT_SECONDS + RECOVERY_MARGIN_SECONDS
    )
    assert response.status_code == 201, response.text
