import httpx

from app.clients.base_client import BaseClient, UpstreamErrors, error_detail
from app.exceptions import (
    DocumentAlreadyExistsError,
    DocumentStoreCircuitOpenError,
    DocumentStoreOverloadedError,
    DocumentStoreRejectedError,
    DocumentStoreTimeoutError,
    DocumentStoreUnavailableError,
    DocumentStoreUnexpectedResponseError,
)
from app.schemas.document_store import NewDocument, StoredDocument

DOCUMENTS_PATH = "/api/v1/documents"

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
