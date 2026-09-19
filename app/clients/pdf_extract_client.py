import httpx
from pydantic import ValidationError

from app.clients.base_client import BaseClient
from app.clients.circuit_breaker import CircuitOpenError
from app.exceptions import (
    PdfExtractCircuitOpenError,
    PdfExtractRejectedError,
    PdfExtractTimeoutError,
    PdfExtractUnavailableError,
    PdfExtractUnexpectedResponseError,
)
from app.schemas.documents import PdfDocument
from app.schemas.pdf_extract import ExtractedDocument

DOCUMENTS_PATH = "/api/v1/documents"


class PdfExtractClient(BaseClient):
    """Cliente del microservicio de extracción de texto (pdf-extractext).

    Traduce el contrato HTTP del hoja (multipart de entrada, JSON y Problem
    Details de salida) a objetos y excepciones de dominio del orquestador.
    """

    async def create_document(self, document: PdfDocument, name: str) -> ExtractedDocument:
        """`POST /api/v1/documents`: envía el PDF y devuelve el documento con su texto."""
        try:
            response = await self._request(
                "POST",
                DOCUMENTS_PATH,
                data={"name": name},
                files={"file": (document.filename, document.content, document.content_type)},
            )
        except CircuitOpenError as exc:
            raise PdfExtractCircuitOpenError(
                "pdf-extractext está temporalmente deshabilitado por fallos repetidos; "
                f"reintentar en {exc.retry_after_seconds:.0f} s."
            ) from exc
        except httpx.TimeoutException as exc:
            raise PdfExtractTimeoutError(
                "pdf-extractext no respondió dentro del timeout configurado."
            ) from exc
        except httpx.TransportError as exc:
            raise PdfExtractUnavailableError("No se pudo conectar con pdf-extractext.") from exc

        if response.status_code == httpx.codes.CREATED:
            return self._parse_document(response)

        detail = self._error_detail(response)
        if response.is_client_error:
            raise PdfExtractRejectedError(detail)
        raise PdfExtractUnexpectedResponseError(
            f"pdf-extractext respondió {response.status_code}: {detail}"
        )

    @staticmethod
    def _parse_document(response: httpx.Response) -> ExtractedDocument:
        try:
            return ExtractedDocument.model_validate_json(response.content)
        except ValidationError as exc:
            raise PdfExtractUnexpectedResponseError(
                "La respuesta de pdf-extractext no tiene el formato esperado."
            ) from exc

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """Extrae `detail` de un Problem Details; si no hay, usa el cuerpo crudo."""
        try:
            body = response.json()
        except ValueError:
            return response.text
        if isinstance(body, dict) and isinstance(body.get("detail"), str):
            return body["detail"]
        return response.text
