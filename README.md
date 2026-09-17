# orchestrator-service

Microservicio orquestador construido con **FastAPI**. Recibe solicitudes y coordina llamadas a otros microservicios. No mantiene estado ni se conecta a bases de datos.

## Arquitectura

Arquitectura de 3 capas con patrón MVC:

| Capa | Ubicación | Responsabilidad |
|---|---|---|
| Presentación | `app/routers/` + `app/schemas/` | HTTP, validación de entrada (Pydantic), respuestas |
| Aplicación | `app/services/` | Orquestación y coordinación entre microservicios |
| Infraestructura de salida | `app/clients/` | Clients HTTP (httpx) hacia cada microservicio |

Regla de dependencias: **Router → Service → Client**. La inyección se realiza con `fastapi.Depends` (ver `app/dependencies.py`) y la configuración centralizada en `app/config.py` (`pydantic-settings`).

Los clientes heredan de `app/clients/base_client.py`, punto de extensión donde se aplicarán los patrones de resiliencia (Retry, Circuit Breaker, Bulkhead).

Las decisiones de diseño se registran como ADRs en [`docs/decisions/`](docs/decisions/).

## Requisitos

- [uv](https://docs.astral.sh/uv/)

## Uso local

```bash
uv sync                          # instala dependencias (crea .venv)
uv run python main.py            # levanta el servicio en :8000 con reload
```

Documentación interactiva: `http://localhost:8000/docs`

## Tests

```bash
uv run pytest                    # tests unitarios e integración
uv run pytest tests/system       # tests de sistema (requieren servicios)
```

## Docker

```bash
docker build -t orchestrator-service .
docker run -p 8000:8000 orchestrator-service
```
