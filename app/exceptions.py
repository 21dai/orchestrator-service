"""Excepciones de dominio del orquestador.

Los services y clients las lanzan; los routers nunca las capturan a mano: se
traducen a respuestas HTTP en un solo lugar (`app/errors.py`).
"""


class OrchestratorError(Exception):
    """Base de todas las excepciones de dominio.

    `retry_after_seconds`, si se indica, le dice al cliente cuándo tiene
    sentido volver a intentar: `app/errors.py` lo manda en el header
    `Retry-After`.
    """

    def __init__(self, detail: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(detail)
        self.retry_after_seconds = retry_after_seconds


class InvalidPdfError(OrchestratorError):
    """El archivo recibido no es un PDF válido."""


class InvalidDocumentNameError(OrchestratorError):
    """El nombre del documento no es válido para document-store (HTTP 400)."""


class PdfTooLargeError(OrchestratorError):
    """El archivo recibido supera el tamaño máximo permitido (HTTP 413)."""

    def __init__(self, max_size_bytes: int) -> None:
        super().__init__(
            f"El archivo supera el tamaño máximo permitido de {max_size_bytes} bytes "
            f"({max_size_bytes / (1024 * 1024):g} MB)."
        )
        self.max_size_bytes = max_size_bytes


class PdfExtractError(OrchestratorError):
    """Base de los fallos al llamar al microservicio pdf-extractext."""


class PdfExtractRejectedError(PdfExtractError):
    """pdf-extractext rechazó el PDF (4xx): p. ej. un PDF dañado del que no puede extraer."""


class PdfExtractUnexpectedResponseError(PdfExtractError):
    """pdf-extractext respondió algo que no se puede interpretar (5xx o body inválido)."""


class PdfExtractUnavailableError(PdfExtractError):
    """pdf-extractext no está disponible.

    No se pudo conectar, no quedan réplicas sanas o el circuito está abierto en
    Traefik (502, o 503 sin Retry-After).
    """


class PdfExtractTimeoutError(PdfExtractError):
    """pdf-extractext no respondió dentro del timeout configurado."""


class PdfExtractOverloadedError(PdfExtractError):
    """pdf-extractext no da abasto: el bulkhead está lleno o su cola respondió 503."""


class DocumentStoreError(OrchestratorError):
    """Base de los fallos al llamar al microservicio document-store."""


class DocumentAlreadyExistsError(DocumentStoreError):
    """document-store ya tiene un documento con el mismo checksum (HTTP 409)."""

    def __init__(self, detail: str, checksum: str) -> None:
        super().__init__(detail)
        self.checksum = checksum


class DocumentNotFoundError(DocumentStoreError):
    """document-store no tiene un documento con ese id (HTTP 404)."""

    def __init__(self, document_id: int) -> None:
        super().__init__(f"No existe un documento con id {document_id}.")
        self.document_id = document_id


class DocumentStoreRejectedError(DocumentStoreError):
    """document-store rechazó los datos con otro 4xx.

    El orquestador ya validó lo que manda: un rechazo es un contrato roto o
    una URL mal configurada, no un error del cliente (HTTP 502).
    """


class DocumentStoreUnexpectedResponseError(DocumentStoreError):
    """document-store respondió algo que no se puede interpretar (5xx o body inválido)."""


class DocumentStoreUnavailableError(DocumentStoreError):
    """document-store no está disponible.

    No se pudo conectar, su base de datos no responde o el circuito está
    abierto en Traefik (502, o 503 sin Retry-After).
    """


class DocumentStoreTimeoutError(DocumentStoreError):
    """document-store no respondió dentro del timeout configurado."""


class DocumentStoreOverloadedError(DocumentStoreError):
    """document-store no da abasto: el bulkhead está lleno o respondió 503 con Retry-After."""
