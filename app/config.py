from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración del servicio, leída desde variables de entorno."""

    # extra="ignore": un .env compartido puede traer variables de otros
    # servicios (p. ej. IMAGE_TAG); no deben romper el arranque.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "orchestrator-service"
    environment: str = "local"

    # URL base del microservicio de extracción de texto (pdf-extractext).
    # Local: la API corre en :8000. En Docker resuelve por nombre de servicio
    # dentro de la red compartida, p. ej. PDF_EXTRACT_BASE_URL=http://pdf-extractext:8000
    pdf_extract_base_url: str = "http://localhost:8000"

    # Timeout (en segundos) para las llamadas HTTP a otros microservicios.
    # Un POST de un PDF de ~280 páginas tarda ~1,6 s en pdf-extractext, así
    # que debe ser holgado respecto de ese valor.
    http_timeout_seconds: float = 10.0


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración (cacheada por proceso)."""
    return Settings()
