from fastapi import FastAPI

from app.config import get_settings
from app.errors import register_error_handlers
from app.routers import documents, health


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title=settings.app_name)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(documents.router)

    return app


app = create_app()
