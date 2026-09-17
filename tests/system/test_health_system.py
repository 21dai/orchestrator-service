"""Health check contra el stack desplegado (opt-in, ver tests/system/conftest.py)."""

import os

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SYSTEM_TESTS") != "1",
    reason="Tests de sistema: requieren el stack desplegado (RUN_SYSTEM_TESTS=1).",
)


def test_health_endpoint_responde_ok_en_el_stack_desplegado(http: httpx.Client) -> None:
    response = http.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
