from app.schemas.documents import ReceivedDocument


class DocumentService:
    """Orquesta el flujo de un PDF recibido.

    Por ahora solo toma el archivo y devuelve sus metadatos: la validación,
    la conversión y la llamada al microservicio de extracción se agregan en
    las siguientes issues. No persiste nada (el orquestador no tiene estado).
    """

    async def receive_pdf(
        self,
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> ReceivedDocument:
        return ReceivedDocument(
            filename=filename,
            content_type=content_type,
            size_bytes=len(content),
        )
