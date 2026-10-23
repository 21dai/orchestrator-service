from functools import lru_cache

from pydantic import AliasChoices, Field
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

    # URL base del microservicio que persiste los documentos (document-store).
    # Local corre en :8002 (pdf-extractext usa :8000 y el orquestador :8001). En Docker,
    # DOCUMENT_STORE_BASE_URL=http://document-store:8000
    document_store_base_url: str = "http://localhost:8002"

    # Timeout de lectura/escritura (en segundos) de cada microservicio.
    # pdf-extractext responde 503 antes de 25 s si no llega a extraer a tiempo
    # (su EXTRACT_MAX_WAIT_SECONDS): el timeout tiene que ser mayor para
    # recibir ese 503 en lugar de cortar una extracción que iba a terminar.
    # HTTP_TIMEOUT_SECONDS es el nombre anterior y se sigue aceptando.
    pdf_extract_timeout_seconds: float = Field(
        default=30.0,
        validation_alias=AliasChoices("PDF_EXTRACT_TIMEOUT_SECONDS", "HTTP_TIMEOUT_SECONDS"),
    )
    # document-store solo guarda un JSON en MongoDB: si tarda más, algo anda mal.
    document_store_timeout_seconds: float = 5.0

    # Timeout solo para establecer la conexión, el mismo para los dos. Más
    # corto que los anteriores: si un servicio está caído, cada intento falla
    # en 2 s en vez de esperar el timeout completo.
    http_connect_timeout_seconds: float = 2.0

    # Retry (ver app/clients/retry.py). Solo se reintentan fallos de conexión
    # y 5xx en métodos idempotentes: máximo de intentos (contando el primero)
    # y espera exponencial con jitter, en segundos.
    retry_max_attempts: int = 3
    retry_backoff_seconds: float = 0.2
    retry_max_backoff_seconds: float = 2.0

    # Circuit Breaker (ver app/clients/circuit_breaker.py): fallos consecutivos
    # (errores de transporte, timeouts, 5xx) para abrir el circuito, y segundos
    # que permanece abierto antes de probar una llamada (half-open).
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_seconds: float = 30.0

    # Bulkhead (ver app/clients/bulkhead.py): requests en vuelo hacia el hoja,
    # cuántas pueden esperar un lugar y por cuántos segundos. Peor caso en
    # memoria por proceso: (max_concurrent + max_waiting) PDFs de hasta 10 MB.
    bulkhead_max_concurrent: int = 5
    bulkhead_max_waiting: int = 10
    bulkhead_acquire_timeout_seconds: float = 5.0

    # Tamaño máximo del PDF, el mismo que acepta pdf-extractext. Más grande se
    # rechaza con 413 sin terminar de leerlo; lo aceptado queda en memoria y
    # nunca se escribe a disco (ADR-0001). Acota la memoria del Bulkhead.
    max_pdf_size_bytes: int = 10 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración (cacheada por proceso)."""
    return Settings()
