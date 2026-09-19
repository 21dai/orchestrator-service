from pathlib import Path

from app.config import Settings


def test_settings_tiene_defaults_para_desarrollo_local() -> None:
    settings = Settings(_env_file=None)

    assert settings.pdf_extract_base_url == "http://localhost:8000"
    assert settings.http_timeout_seconds == 10.0


def test_settings_lee_variables_del_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PDF_EXTRACT_BASE_URL=http://pdf-extractext:8000\nHTTP_TIMEOUT_SECONDS=30\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.pdf_extract_base_url == "http://pdf-extractext:8000"
    assert settings.http_timeout_seconds == 30.0


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
