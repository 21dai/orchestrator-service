"""Infraestructura de los tests de sistema (opt-in).

Validan el comportamiento punta a punta contra el stack real desplegado
(Traefik + orchestrator + pdf-extractext + MongoDB):

1. Levantar el stack:  cd ../infrastructure && docker compose up -d --build
2. Correr la suite:    RUN_SYSTEM_TESTS=1 uv run pytest tests/system -o addopts=""

`-o addopts=""` evita que el umbral de cobertura de la suite regular aplique a
estos tests. Con `ORCHESTRATOR_BASE_URL` se apunta a otro despliegue (default:
Traefik local). Cada módulo de esta carpeta declara `pytestmark` para saltearse
sin `RUN_SYSTEM_TESTS=1`.
"""

import os
import uuid
from collections.abc import Iterator

import httpx
import pytest


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

    El token cambia los bytes => cambia el SHA-256: cada corrida registra un
    documento nuevo y pdf-extractext no lo rechaza por checksum duplicado, aun
    con la base MongoDB persistente entre corridas.
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
