"""DocumentUpdate: el body del PUT se valida en el orquestador, con las mismas
reglas que document-store, para responder 422 sin llegar a llamarlo."""

import pytest
from pydantic import ValidationError

from app.schemas.documents import DocumentUpdate


def test_acepta_un_nombre_y_le_quita_los_espacios_de_los_extremos() -> None:
    assert DocumentUpdate.model_validate({"name": "  Nuevo nombre  "}).name == "Nuevo nombre"


@pytest.mark.parametrize("length", [1, 255])
def test_acepta_nombres_de_1_a_255_caracteres(length: int) -> None:
    assert len(DocumentUpdate.model_validate({"name": "x" * length}).name) == length


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 256},
        {"name": None},
        {"name": 123},
        {"name": True},
        {"name": ["informe"]},
        {"name": "informe", "checksum": "a" * 64},
    ],
    ids=[
        "sin-name",
        "vacio",
        "solo-espacios",
        "256-caracteres",
        "null",
        "numero",
        "booleano",
        "lista",
        "campo-extra",
    ],
)
def test_rechaza_lo_mismo_que_document_store(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DocumentUpdate.model_validate(body)
