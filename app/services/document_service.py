from app.schemas.documents import PdfDocument, ReceivedDocument


class DocumentService:
    """Orquesta el flujo de un PDF ya validado y convertido (`PdfDocument`).

    Por ahora devuelve sus metadatos: la llamada al microservicio de
    extracción se agrega en las siguientes issues. No persiste nada (el
    orquestador no tiene estado).
    """

    async def receive_pdf(self, document: PdfDocument) -> ReceivedDocument:
        return ReceivedDocument.from_pdf(document)
