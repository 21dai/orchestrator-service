from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.dependencies import close_pdf_extract_client
from app.errors import register_error_handlers
from app.routers import documents, health


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ciclo de vida de la app: al apagar, cierra los clients HTTP salientes."""
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
