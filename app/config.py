from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración del servicio, leída desde variables de entorno."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "orchestrator-service"
    environment: str = "local"

    # URLs base de los microservicios orquestados.
    # En despliegue, resuelven por nombre de servicio detrás de Traefik.
    # Ejemplo: SERVICE_A_BASE_URL=http://service-a:8000
    service_a_base_url: str = "http://localhost:8001"

    # Timeout (en segundos) para las llamadas HTTP a otros microservicios.
    http_timeout_seconds: float = 10.0


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración (cacheada por proceso)."""
    return Settings()
