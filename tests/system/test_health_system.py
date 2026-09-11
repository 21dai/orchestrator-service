"""Placeholder de tests de sistema.

Los tests de sistema se ejecutan contra el servicio desplegado
(ej. con Docker) y validan el comportamiento de punta a punta.
No usan mocks: requieren servicios reales o el compose de integración.

Ejecución explícita:
    uv run pytest tests/system
"""

import os

import httpx
import pytest

BASE_URL = os.environ.get("ORCHESTRATOR_BASE_URL", "http://localhost:8000")


@pytest.mark.skipif(
    os.environ.get("RUN_SYSTEM_TESTS") != "1",
    reason="Tests de sistema: requieren servicio desplegado (RUN_SYSTEM_TESTS=1)",
)
async def test_health_endpoint_sistema() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
