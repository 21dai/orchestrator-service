from app.schemas.documents import ReceivedDocument
from app.services.pdf_validator import validate_pdf


class DocumentService:
    """Orquesta el flujo de un PDF recibido.

    Por ahora valida el archivo y devuelve sus metadatos: la conversión y la
    llamada al microservicio de extracción se agregan en las siguientes
    issues. No persiste nada (el orquestador no tiene estado).
    """

    async def receive_pdf(
        self,
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> ReceivedDocument:
        validate_pdf(filename=filename, content_type=content_type, content=content)

        return ReceivedDocument(
            filename=filename,
            content_type=content_type,
            size_bytes=len(content),
        )
