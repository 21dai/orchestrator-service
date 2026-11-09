from typing import NoReturn

import httpx

from app.clients.base_client import BaseClient, UpstreamErrors, error_detail
from app.exceptions import (
    DocumentAlreadyExistsError,
    DocumentNotFoundError,
    DocumentStoreCircuitOpenError,
    DocumentStoreOverloadedError,
    DocumentStoreRejectedError,
    DocumentStoreTimeoutError,
    DocumentStoreUnavailableError,
    DocumentStoreUnexpectedResponseError,
)
from app.schemas.document_store import NewDocument, StoredDocument

DOCUMENTS_PATH = "/api/v1/documents"


def document_path(document_id: int) -> str:
    return f"{DOCUMENTS_PATH}/{document_id}"


ERRORS = UpstreamErrors(
    service="document-store",
    unavailable=DocumentStoreUnavailableError,
    timeout=DocumentStoreTimeoutError,
    circuit_open=DocumentStoreCircuitOpenError,
    overloaded=DocumentStoreOverloadedError,
    unexpected=DocumentStoreUnexpectedResponseError,
)


class DocumentStoreClient(BaseClient):
    """Cliente del microservicio que persiste los documentos (document-store).

    Traduce su contrato HTTP (JSON de entrada, JSON y Problem Details de
    salida) a objetos y excepciones de dominio del orquestador.
    """

    async def create_document(self, document: NewDocument) -> StoredDocument:
        """`POST /api/v1/documents`: guarda el documento y lo devuelve con su id."""
        response = await self._call(ERRORS, "POST", DOCUMENTS_PATH, json=document.model_dump())

        if response.status_code == httpx.codes.CREATED:
            return self._parse(ERRORS, response, StoredDocument)
        if response.status_code == httpx.codes.CONFLICT:
            raise DocumentAlreadyExistsError(error_detail(response), checksum=document.checksum)
        if response.is_client_error:
            raise DocumentStoreRejectedError(
                f"document-store rechazó el documento: {error_detail(response)}"
            )
        self._raise_unexpected(ERRORS, response)

    async def get_document(self, document_id: int) -> StoredDocument:
        """`GET /api/v1/documents/{id}`: el documento guardado con ese id."""
        response = await self._call(ERRORS, "GET", document_path(document_id))

        if response.status_code == httpx.codes.OK:
            return self._parse(ERRORS, response, StoredDocument)
        self._raise_for_document(response, document_id)

    def _raise_for_document(self, response: httpx.Response, document_id: int) -> NoReturn:
        """Errores de las operaciones sobre un documento por id.

        Un 404 es que el id no existe. Otro 4xx es un pedido que el orquestador
        ya debería haber validado (contrato roto, HTTP 502), como en el alta.
        """
        if response.status_code == httpx.codes.NOT_FOUND:
            raise DocumentNotFoundError(document_id)
        if response.is_client_error:
            raise DocumentStoreRejectedError(
                f"document-store rechazó la solicitud: {error_detail(response)}"
            )
        self._raise_unexpected(ERRORS, response)
