"""Infraestructura de los tests de sistema (opt-in).

Validan el flujo punta a punta contra el stack real desplegado en
`../infrastructure`:

    Cliente → Traefik → orchestrator → pdf-extractext (POST /extract, sin estado)
                                     → document-store (POST /api/v1/documents) → MongoDB

1. Levantar el stack:  cd ../infrastructure && docker compose up -d --build
2. Correr la suite:    RUN_SYSTEM_TESTS=1 uv run pytest tests/system -o addopts=""
3. Con fallas:         RUN_SYSTEM_TESTS=1 RUN_FAULT_TESTS=1 uv run pytest tests/system -o addopts=""

`-o addopts=""` evita que el umbral de cobertura de la suite regular aplique a
estos tests. Con `ORCHESTRATOR_BASE_URL` se apunta a otro despliegue (default:
Traefik local) y con `INFRASTRUCTURE_DIR` a otra carpeta del compose (default:
`../infrastructure`, hermana de este repo). Cada módulo de esta carpeta declara
`pytestmark` para saltearse sin `RUN_SYSTEM_TESTS=1`.
"""

import json
import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("ORCHESTRATOR_BASE_URL", "https://orchestrator.proyecto.localhost")


@pytest.fixture(scope="session")
def http(base_url: str) -> Iterator[httpx.Client]:
    """Cliente HTTP real. verify=False: Traefik usa certificados locales autofirmados."""
    with httpx.Client(base_url=base_url, verify=False, timeout=30) as client:
        yield client


def build_pdf(token: str) -> bytes:
    """PDF mínimo válido (con xref) y un token único embebido.

    El token cambia los bytes => cambia el SHA-256: cada test registra un
    documento nuevo y document-store no responde 409 por checksum duplicado,
    aun con la base MongoDB persistente entre corridas.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
    ]
    body = bytearray(b"%PDF-1.4\n% token: " + token.encode() + b"\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_position = len(body)
    body += f"xref\n0 {len(objects) + 1}\n".encode()
    body += b"0000000000 65535 f \n"
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode()
    body += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF\n"
    ).encode()
    return bytes(body)


@pytest.fixture
def pdf_file() -> tuple[str, bytes]:
    """(filename, contenido) de un PDF único por test."""
    token = uuid.uuid4().hex
    return f"sistema-{token}.pdf", build_pdf(token)


@pytest.fixture
def otro_pdf_file() -> tuple[str, bytes]:
    """Un segundo PDF único, distinto de `pdf_file`, para los tests que mandan dos."""
    token = uuid.uuid4().hex
    return f"sistema-otro-{token}.pdf", build_pdf(token)


@pytest.fixture
def damaged_pdf_file() -> tuple[str, bytes]:
    """(filename, contenido) de un PDF dañado.

    Empieza con `%PDF-`, así que pasa la validación del orquestador
    (extensión, MIME y firma) y llega a pdf-extractext, pero no tiene objetos
    ni xref: PDFium no lo puede abrir y pdf-extractext lo rechaza.
    """
    token = uuid.uuid4().hex
    content = b"%PDF-1.4\n% token: " + token.encode() + b"\nesto no es un PDF de verdad\n"
    return f"sistema-danado-{token}.pdf", content


# --- docker compose sobre el stack de ../infrastructure ---


class DockerCompose:
    """Corre `docker compose` en la carpeta del stack (la de `INFRASTRUCTURE_DIR`).

    Respeta las variables de Docker Compose del entorno (`COMPOSE_FILE`,
    `COMPOSE_PROJECT_NAME`), así que apunta al mismo proyecto con el que se
    levantó el stack.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def run(self, *args: str, timeout: float = 120) -> str:
        result = subprocess.run(
            ["docker", "compose", *args],
            cwd=self.directory,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"`docker compose {' '.join(args)}` falló ({result.returncode}):\n{result.stderr}"
            )
        return result.stdout

    def stop(self, service: str) -> None:
        self.run("stop", service)

    def start(self, service: str) -> None:
        self.run("start", service)

    def health(self, service: str) -> list[str]:
        """Estado de salud de cada contenedor del servicio (`healthy`, `starting`, ...)."""
        output = self.run("ps", "--all", "--format", "json", service).strip()
        if not output:
            return []
        # Según la versión, Compose imprime un array JSON o un objeto por línea.
        if output.startswith("["):
            containers = json.loads(output)
        else:
            containers = [json.loads(line) for line in output.splitlines() if line.strip()]
        return [container.get("Health") or container.get("State", "") for container in containers]

    def wait_healthy(self, service: str, timeout: float = 120) -> None:
        """Espera a que todos los contenedores del servicio estén `healthy`."""
        deadline = time.monotonic() + timeout
        states: list[str] = []
        while time.monotonic() < deadline:
            states = self.health(service)
            if states and all(state == "healthy" for state in states):
                return
            time.sleep(1)
        raise TimeoutError(f"{service} no quedó healthy en {timeout:g} s (estado: {states}).")


@pytest.fixture(scope="session")
def infrastructure_dir() -> Path:
    directory = Path(os.environ.get("INFRASTRUCTURE_DIR", REPO_ROOT.parent / "infrastructure"))
    if not (directory / "docker-compose.yml").is_file():
        pytest.fail(
            f"No se encontró docker-compose.yml en {directory}. "
            "Indicar la carpeta del stack con INFRASTRUCTURE_DIR."
        )
    return directory


@pytest.fixture(scope="session")
def compose(infrastructure_dir: Path) -> DockerCompose:
    return DockerCompose(infrastructure_dir)
