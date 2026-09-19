"""Excepciones de dominio del orquestador.

Los services y clients las lanzan; los routers nunca las capturan a mano: se
traducen a respuestas HTTP en un solo lugar (`app/errors.py`).
"""


class OrchestratorError(Exception):
    """Base de todas las excepciones de dominio."""


class InvalidPdfError(OrchestratorError):
    """El archivo recibido no es un PDF válido."""


class PdfExtractError(OrchestratorError):
    """Base de los fallos al llamar al microservicio pdf-extractext."""


class PdfExtractRejectedError(PdfExtractError):
    """pdf-extractext rechazó el documento (4xx): p. ej. checksum duplicado."""


class PdfExtractUnexpectedResponseError(PdfExtractError):
    """pdf-extractext respondió algo que no se puede interpretar (5xx o body inválido)."""


class PdfExtractUnavailableError(PdfExtractError):
    """No se pudo conectar con pdf-extractext."""


class PdfExtractTimeoutError(PdfExtractError):
    """pdf-extractext no respondió dentro del timeout configurado."""


class PdfExtractCircuitOpenError(PdfExtractError):
    """pdf-extractext está temporalmente deshabilitado: el circuit breaker está abierto."""


class PdfExtractOverloadedError(PdfExtractError):
    """Demasiadas solicitudes en curso hacia pdf-extractext: el bulkhead está lleno."""
