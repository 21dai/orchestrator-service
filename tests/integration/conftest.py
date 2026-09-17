from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """TestClient con lifespan completo: levanta y cierra la app en cada test."""
    with TestClient(app) as test_client:
        yield test_client
