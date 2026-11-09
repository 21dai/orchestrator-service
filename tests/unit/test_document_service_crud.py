"""DocumentService: listar, ver, renombrar y borrar documentos de document-store.

El orquestador no guarda nada: delega en document-store (simulado acá con un
fake en memoria) y traduce cada documento a la respuesta pública.
"""

from datetime import UTC, datetime

import pytest

from app.exceptions import DocumentNotFoundError, DocumentStoreUnavailableError
from app.schemas.document_store import NewDocument, StoredDocument
from app.schemas.documents import PdfDocument, ProcessedDocument
from app.schemas.pdf_extract import ExtractionResult
from app.services.document_service import DocumentService

CREATED_AT = datetime(2026, 10, 23, 10, 0, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 11, 9, 15, 30, 0, tzinfo=UTC)


def stored(document_id: int, name: str = "informe") -> StoredDocument:
    return StoredDocument(
        id=document_id,
        name=name,
        original_filename=f"{name}.pdf",
        checksum=f"{document_id:064x}",
        file_size=1024,
        page_count=3,
        extracted_text="# Informe",
        is_processed=True,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )


class FakeDocumentStore:
    """document-store en memoria, con el mismo contrato que el client."""

    def __init__(self, *documents: StoredDocument, error: Exception | None = None) -> None:
        self.documents = {document.id: document for document in documents}
        self.error = error
        self.list_calls: list[tuple[int, int]] = []

    def _check(self) -> None:
        if self.error is not None:
            raise self.error

    async def create_document(self, document: NewDocument) -> StoredDocument:  # pragma: no cover
        raise AssertionError("el CRUD no da de alta documentos")

    async def list_documents(self, skip: int, limit: int) -> list[StoredDocument]:
        self._check()
        self.list_calls.append((skip, limit))
        return sorted(self.documents.values(), key=lambda d: d.id)[skip : skip + limit]

    async def get_document(self, document_id: int) -> StoredDocument:
        self._check()
        if document_id not in self.documents:
            raise DocumentNotFoundError(document_id)
        return self.documents[document_id]

    async def update_document(self, document_id: int, name: str) -> StoredDocument:
        current = await self.get_document(document_id)
        renamed = current.model_copy(update={"name": name, "updated_at": UPDATED_AT})
        self.documents[document_id] = renamed
        return renamed

    async def delete_document(self, document_id: int) -> None:
        await self.get_document(document_id)
        del self.documents[document_id]


class UnusedPdfExtract:
    """El CRUD nunca extrae texto."""

    async def extract(self, document: PdfDocument) -> ExtractionResult:  # pragma: no cover
        raise AssertionError("el CRUD no llama a pdf-extractext")


def make_service(store: FakeDocumentStore) -> DocumentService:
    return DocumentService(pdf_extract_client=UnusedPdfExtract(), document_store_client=store)


async def test_list_documents_pide_la_pagina_y_traduce_cada_documento() -> None:
    store = FakeDocumentStore(stored(1), stored(2), stored(3))

    result = await make_service(store).list_documents(skip=1, limit=1)

    assert store.list_calls == [(1, 1)]
    assert result == [ProcessedDocument.from_stored(stored(2))]


async def test_list_documents_devuelve_una_lista_vacia_si_no_hay_documentos() -> None:
    assert await make_service(FakeDocumentStore()).list_documents(skip=0, limit=10) == []


async def test_get_document_devuelve_el_documento_traducido() -> None:
    store = FakeDocumentStore(stored(7))

    result = await make_service(store).get_document(7)

    assert result == ProcessedDocument.from_stored(stored(7))


async def test_update_document_renombra_y_devuelve_el_documento_actualizado() -> None:
    store = FakeDocumentStore(stored(7))

    result = await make_service(store).update_document(7, name="nuevo nombre")

    assert result.name == "nuevo nombre"
    assert result.updated_at == UPDATED_AT
    assert result.processed_at == CREATED_AT
    assert store.documents[7].name == "nuevo nombre"


async def test_delete_document_borra_el_documento() -> None:
    store = FakeDocumentStore(stored(7), stored(8))

    assert await make_service(store).delete_document(7) is None

    assert list(store.documents) == [8]


@pytest.mark.parametrize(
    "operation",
    [
        lambda service: service.get_document(99),
        lambda service: service.update_document(99, name="x"),
        lambda service: service.delete_document(99),
    ],
    ids=["ver", "renombrar", "borrar"],
)
async def test_un_id_inexistente_propaga_documento_no_encontrado(operation) -> None:
    store = FakeDocumentStore(stored(7))

    with pytest.raises(DocumentNotFoundError, match="id 99"):
        await operation(make_service(store))

    assert list(store.documents) == [7]


@pytest.mark.parametrize(
    "operation",
    [
        lambda service: service.list_documents(skip=0, limit=10),
        lambda service: service.get_document(7),
        lambda service: service.update_document(7, name="x"),
        lambda service: service.delete_document(7),
    ],
    ids=["listar", "ver", "renombrar", "borrar"],
)
async def test_los_fallos_de_document_store_se_propagan(operation) -> None:
    store = FakeDocumentStore(stored(7), error=DocumentStoreUnavailableError("caído"))

    with pytest.raises(DocumentStoreUnavailableError):
        await operation(make_service(store))
