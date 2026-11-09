"""main.py (raíz del repo) arranca uvicorn con lo que dice Settings, sin valores fijos."""

from unittest.mock import patch

import pytest

import main
from app.config import Settings


@pytest.fixture
def uvicorn_run():
    with patch.object(main.uvicorn, "run") as run:
        yield run


def _arrancar_con(settings: Settings, uvicorn_run) -> dict:
    with patch.object(main, "get_settings", return_value=settings):
        main.main()
    uvicorn_run.assert_called_once()
    args, kwargs = uvicorn_run.call_args
    assert args == ("app.main:app",)
    return kwargs


def test_main_usa_host_y_port_de_la_configuracion(uvicorn_run) -> None:
    kwargs = _arrancar_con(Settings(_env_file=None, host="127.0.0.1", port=9000), uvicorn_run)

    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 9000


def test_main_sin_debug_no_activa_reload(uvicorn_run) -> None:
    kwargs = _arrancar_con(Settings(_env_file=None), uvicorn_run)

    assert kwargs["reload"] is False


def test_main_con_debug_activa_reload(uvicorn_run) -> None:
    kwargs = _arrancar_con(Settings(_env_file=None, debug=True), uvicorn_run)

    assert kwargs["reload"] is True


def test_main_solo_confia_en_los_proxies_configurados(uvicorn_run) -> None:
    kwargs = _arrancar_con(
        Settings(_env_file=None, forwarded_allow_ips="172.28.0.0/16"), uvicorn_run
    )

    assert kwargs["proxy_headers"] is True
    assert kwargs["forwarded_allow_ips"] == "172.28.0.0/16"


def test_main_configura_el_apagado_ordenado(uvicorn_run) -> None:
    kwargs = _arrancar_con(Settings(_env_file=None, graceful_shutdown_seconds=20), uvicorn_run)

    assert kwargs["timeout_graceful_shutdown"] == 20
