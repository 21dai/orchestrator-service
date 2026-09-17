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
