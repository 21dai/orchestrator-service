"""Fallas de persistencia contra el stack real: se detiene document-store.

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

# Mismo valor que CIRCUIT_BREAKER_RECOVERY_SECONDS del orquestador desplegado
# (default 30 s): lo máximo que el circuito puede seguir abierto.
CIRCUIT_BREAKER_RECOVERY_SECONDS = float(os.environ.get("CIRCUIT_BREAKER_RECOVERY_SECONDS", "30"))
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
    durante la caída se acumularon `CIRCUIT_BREAKER_FAILURE_THRESHOLD` fallos
    (cada request suma uno por intento del Retry), el Circuit Breaker del
    orquestador queda abierto y responde 503 sin llamar a nadie hasta que
    pasan `CIRCUIT_BREAKER_RECOVERY_SECONDS`. En lugar de un `sleep` fijo de
    ese tiempo (lento cuando el circuito nunca se abrió), se reintenta cada
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

    # El orquestador no llega a document-store (tras los reintentos de
    # conexión): 503 Problem Details que nombra al servicio caído.
    assert caido.status_code == 503, caido.text
    assert caido.headers["content-type"] == "application/problem+json"
    body = caido.json()
    assert body["status"] == 503
    assert DOCUMENT_STORE in body["detail"]

    document_store_down()

    # El mismo PDF se guarda: si el intento fallido hubiera dejado algo a
    # medias con ese checksum, document-store respondería 409.
    response = post_until_not_503(
        http, pdf_file, timeout=CIRCUIT_BREAKER_RECOVERY_SECONDS + RECOVERY_MARGIN_SECONDS
    )
    assert response.status_code == 201, response.text
