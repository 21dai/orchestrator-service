from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.dependencies import close_pdf_extract_client, get_pdf_extract_client
from app.errors import register_error_handlers
from app.routers import documents, health


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ciclo de vida de la app.

    Al arrancar crea los clients HTTP salientes, para que Retry, Circuit
    Breaker y Bulkhead existan una sola vez por proceso antes de la primera
    request (si se crearan perezosamente, varias requests simultáneas podrían
    construir cada una su propio client). Al apagar, los cierra.
    """
    get_pdf_extract_client()
    yield
    await close_pdf_extract_client()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(documents.router)

    return app


app = create_app()
