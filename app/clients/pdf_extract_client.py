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
from app.schemas.pdf_extract import ExtractionResult

EXTRACT_PATH = "/extract"

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

    async def extract(self, document: PdfDocument) -> ExtractionResult:
        """`POST /extract`: envía el PDF y devuelve su texto y cantidad de páginas.

        Es el endpoint sin estado de pdf-extractext: no guarda nada, así que
        el mismo PDF se puede mandar las veces que haga falta. Guardar el
        documento es trabajo de document-store.
        """
        response = await self._call(
            ERRORS,
            "POST",
            EXTRACT_PATH,
            files={"file": (document.filename, document.content, document.content_type)},
        )

        if response.status_code == httpx.codes.OK:
            return self._parse(ERRORS, response, ExtractionResult)
        if response.is_client_error:
            raise PdfExtractRejectedError(error_detail(response))
        self._raise_unexpected(ERRORS, response)
