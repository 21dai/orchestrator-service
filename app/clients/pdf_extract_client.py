import httpx

from app.clients.base_client import BaseClient, UpstreamErrors, error_detail
from app.exceptions import (
    PdfExtractCircuitOpenError,
    PdfExtractOverloadedError,
    PdfExtractRejectedError,
    PdfExtractTimeoutError,
    PdfExtractUnavailableError,
    PdfExtractUnexpectedResponseError,
)
from app.schemas.documents import PdfDocument
from app.schemas.pdf_extract import ExtractedDocument

DOCUMENTS_PATH = "/api/v1/documents"

ERRORS = UpstreamErrors(
    service="pdf-extractext",
    unavailable=PdfExtractUnavailableError,
    timeout=PdfExtractTimeoutError,
    circuit_open=PdfExtractCircuitOpenError,
    overloaded=PdfExtractOverloadedError,
    unexpected=PdfExtractUnexpectedResponseError,
)


class PdfExtractClient(BaseClient):
    """Cliente del microservicio de extracción de texto (pdf-extractext).

    Traduce el contrato HTTP del hoja (multipart de entrada, JSON y Problem
    Details de salida) a objetos y excepciones de dominio del orquestador.
    """

    async def create_document(self, document: PdfDocument, name: str) -> ExtractedDocument:
        """`POST /api/v1/documents`: envía el PDF y devuelve el documento con su texto."""
        response = await self._call(
            ERRORS,
            "POST",
            DOCUMENTS_PATH,
            data={"name": name},
            files={"file": (document.filename, document.content, document.content_type)},
        )

        if response.status_code == httpx.codes.CREATED:
            return self._parse(ERRORS, response, ExtractedDocument)
        if response.is_client_error:
            raise PdfExtractRejectedError(error_detail(response))
        self._raise_unexpected(ERRORS, response)
