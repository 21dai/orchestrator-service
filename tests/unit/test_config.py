from pathlib import Path

from app.config import Settings


def test_settings_tiene_defaults_para_desarrollo_local() -> None:
    settings = Settings(_env_file=None)

    assert settings.pdf_extract_base_url == "http://localhost:8000"
    # Local: pdf-extractext en :8000, el orquestador en :8001 y document-store en :8002.
    assert settings.document_store_base_url == "http://localhost:8002"


def test_settings_lee_variables_del_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PDF_EXTRACT_BASE_URL=http://pdf-extractext:8000\n"
        "DOCUMENT_STORE_BASE_URL=http://document-store:8000\n"
        "PDF_EXTRACT_TIMEOUT_SECONDS=40\n"
        "DOCUMENT_STORE_TIMEOUT_SECONDS=3\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.pdf_extract_base_url == "http://pdf-extractext:8000"
    assert settings.document_store_base_url == "http://document-store:8000"
    assert settings.pdf_extract_timeout_seconds == 40.0
    assert settings.document_store_timeout_seconds == 3.0


def test_cada_microservicio_tiene_su_timeout() -> None:
    settings = Settings(_env_file=None)

    # pdf-extractext responde 503 antes de 25 s si no llega a extraer a tiempo
    # (EXTRACT_MAX_WAIT_SECONDS): el timeout del orquestador tiene que ser mayor
    # para recibir ese 503 en vez de cortar una extracción que iba a terminar.
    assert settings.pdf_extract_timeout_seconds == 30.0
    # document-store solo guarda un JSON: si tarda más de 5 s, algo anda mal.
    assert settings.document_store_timeout_seconds == 5.0


def test_http_timeout_seconds_sigue_configurando_el_timeout_de_pdf_extractext(
    tmp_path: Path,
) -> None:
    # Compatibilidad: infrastructure todavía define HTTP_TIMEOUT_SECONDS.
    env_file = tmp_path / ".env"
    env_file.write_text("HTTP_TIMEOUT_SECONDS=60\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.pdf_extract_timeout_seconds == 60.0


def test_settings_ignora_variables_desconocidas_en_el_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("IMAGE_TAG=1.0.0\nOTRA_VARIABLE=x\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.app_name == "orchestrator-service"


def test_settings_tiene_defaults_de_retry() -> None:
    settings = Settings(_env_file=None)

    assert settings.retry_max_attempts == 3
    assert settings.retry_backoff_seconds == 0.2
    assert settings.retry_max_backoff_seconds == 2.0


def test_settings_tiene_defaults_de_circuit_breaker() -> None:
    settings = Settings(_env_file=None)

    assert settings.circuit_breaker_failure_threshold == 5
    assert settings.circuit_breaker_recovery_seconds == 30.0


def test_settings_tiene_defaults_de_bulkhead() -> None:
    settings = Settings(_env_file=None)

    assert settings.bulkhead_max_concurrent == 5
    assert settings.bulkhead_max_waiting == 10
    assert settings.bulkhead_acquire_timeout_seconds == 5.0


def test_settings_tiene_timeout_de_conexion_separado() -> None:
    settings = Settings(_env_file=None)

    assert settings.http_connect_timeout_seconds == 2.0


def test_settings_limita_el_pdf_a_10_mb_como_pdf_extractext() -> None:
    settings = Settings(_env_file=None)

    assert settings.max_pdf_size_bytes == 10 * 1024 * 1024


def test_settings_tiene_defaults_del_servidor() -> None:
    settings = Settings(_env_file=None)

    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    # reload solo en desarrollo: apagado salvo DEBUG=true.
    assert settings.debug is False
    # Solo se confía en X-Forwarded-* de localhost hasta que el compose pase la red de Traefik.
    assert settings.forwarded_allow_ips == "127.0.0.1"
    assert settings.graceful_shutdown_seconds == 60


def test_settings_lee_las_opciones_del_servidor_del_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "HOST=127.0.0.1\n"
        "PORT=9000\n"
        "DEBUG=true\n"
        "FORWARDED_ALLOW_IPS=172.28.0.0/16\n"
        "GRACEFUL_SHUTDOWN_SECONDS=20\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.host == "127.0.0.1"
    assert settings.port == 9000
    assert settings.debug is True
    assert settings.forwarded_allow_ips == "172.28.0.0/16"
    assert settings.graceful_shutdown_seconds == 20
