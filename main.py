"""Punto de entrada del servicio, local (`uv run python main.py`) y en Docker.

Host, puerto y opciones de uvicorn salen de Settings (variables de entorno).
"""

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        # Detrás de Traefik (que termina TLS): tomar esquema y cliente reales de
        # X-Forwarded-*, pero solo si vienen de las IPs/redes configuradas.
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        timeout_graceful_shutdown=settings.graceful_shutdown_seconds,
    )


if __name__ == "__main__":
    main()
