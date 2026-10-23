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
    """No se pudo conectar con pdf-extractext, o no quedan réplicas sanas (503 sin Retry-After)."""


class PdfExtractTimeoutError(PdfExtractError):
    """pdf-extractext no respondió dentro del timeout configurado."""


class PdfExtractCircuitOpenError(PdfExtractError):
    """pdf-extractext está temporalmente deshabilitado: el circuit breaker está abierto."""


class PdfExtractOverloadedError(PdfExtractError):
    """pdf-extractext no da abasto: el bulkhead está lleno o su cola respondió 503."""


class DocumentStoreError(OrchestratorError):
    """Base de los fallos al llamar al microservicio document-store."""


class DocumentAlreadyExistsError(DocumentStoreError):
    """document-store ya tiene un documento con el mismo checksum (HTTP 409)."""

    def __init__(self, detail: str, checksum: str) -> None:
        super().__init__(detail)
        self.checksum = checksum


class DocumentStoreRejectedError(DocumentStoreError):
    """document-store rechazó los datos con otro 4xx.

    El orquestador ya validó lo que manda: un rechazo es un contrato roto o
    una URL mal configurada, no un error del cliente (HTTP 502).
    """


class DocumentStoreUnexpectedResponseError(DocumentStoreError):
    """document-store respondió algo que no se puede interpretar (5xx o body inválido)."""


class DocumentStoreUnavailableError(DocumentStoreError):
    """No se pudo conectar con document-store, o su base de datos no responde (503)."""


class DocumentStoreTimeoutError(DocumentStoreError):
    """document-store no respondió dentro del timeout configurado."""


class DocumentStoreCircuitOpenError(DocumentStoreError):
    """document-store está temporalmente deshabilitado: el circuit breaker está abierto."""


class DocumentStoreOverloadedError(DocumentStoreError):
    """document-store no da abasto: el bulkhead está lleno o respondió 503 con Retry-After."""
