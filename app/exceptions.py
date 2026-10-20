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
    """pdf-extractext rechazó el documento (4xx): p. ej. checksum duplicado."""


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
