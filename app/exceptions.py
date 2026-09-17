"""Excepciones de dominio del orquestador.

Los services las lanzan; los routers nunca las capturan a mano: se traducen a
respuestas HTTP en un solo lugar (`app/errors.py`).
"""


class OrchestratorError(Exception):
    """Base de todas las excepciones de dominio."""


class InvalidPdfError(OrchestratorError):
    """El archivo recibido no es un PDF válido."""
