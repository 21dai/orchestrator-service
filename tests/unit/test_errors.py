import json

from fastapi import Request

from app.errors import problem_response


def make_request(path: str = "/api/v1/documents") -> Request:
    return Request(
        {"type": "http", "method": "POST", "path": path, "headers": [], "query_string": b""}
    )


def test_problem_response_arma_un_problem_details_completo() -> None:
    response = problem_response(make_request(), 400, "detalle del problema")

    assert response.status_code == 400
    assert response.media_type == "application/problem+json"
    payload = json.loads(response.body)
    assert payload["type"] == "about:blank"
    assert payload["title"] == "Bad Request"
    assert payload["status"] == 400
    assert payload["detail"] == "detalle del problema"
    assert payload["instance"].endswith("/api/v1/documents")


def test_problem_response_usa_la_ruta_de_la_request_como_instance() -> None:
    response = problem_response(make_request(path="/otra/ruta"), 503, "sin servicio")

    payload = json.loads(response.body)
    assert payload["status"] == 503
    assert payload["title"] == "Service Unavailable"
    assert payload["instance"].endswith("/otra/ruta")
