from app.exceptions import InvalidPdfError

PDF_EXTENSION = ".pdf"
PDF_CONTENT_TYPE = "application/pdf"
PDF_MAGIC_BYTES = b"%PDF-"
#: Largo máximo del nombre del archivo que acepta document-store.
MAX_FILENAME_LENGTH = 255


def validate_pdf(filename: str, content_type: str | None, content: bytes) -> None:
    """Verifica que el archivo recibido sea un PDF.

    Se chequean tres cosas, de la más barata a la más confiable: la extensión
    del nombre, el tipo MIME declarado por el cliente y la firma real del
    contenido (`%PDF-`). Lanza `InvalidPdfError` con un mensaje claro en el
    primer chequeo que falle.
    """
    if not filename.lower().endswith(PDF_EXTENSION):
        raise InvalidPdfError(
            f"El archivo debe tener extensión {PDF_EXTENSION} (recibido: {filename!r})."
        )

    if len(filename) > MAX_FILENAME_LENGTH:
        raise InvalidPdfError(
            f"El nombre del archivo no puede superar los {MAX_FILENAME_LENGTH} caracteres."
        )

    if content_type != PDF_CONTENT_TYPE:
        raise InvalidPdfError(
            f"El tipo MIME debe ser {PDF_CONTENT_TYPE} (recibido: {content_type!r})."
        )

    if not content:
        raise InvalidPdfError("El archivo está vacío.")

    if not content.startswith(PDF_MAGIC_BYTES):
        raise InvalidPdfError("El contenido del archivo no corresponde a un PDF válido.")
