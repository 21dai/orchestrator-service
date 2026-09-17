# Project Architecture Blueprint — orchestrator-service

> Fecha de generación: 2026-09-11 · Estado: arquitectura inicial (sin lógica de negocio).
> Regenerar este documento cuando la arquitectura evolucione.

## 1. Visión general

Microservicio **orquestador** construido con **FastAPI**. Recibe solicitudes HTTP del API Gateway,
coordina llamadas a los microservicios internos y devuelve respuestas agregadas.

Principios rectores:

- **Sin estado, sin base de datos.** El servicio no persiste nada; toda la información proviene de los microservicios que coordina.
- **Arquitectura de 3 capas + MVC** con regla de dependencias unidireccional **Router → Service → Client**.
- **SOLID y Clean Code**: responsabilidad única por módulo, extensión sin modificación (OCP), inversión de dependencias vía `fastapi.Depends`.
- **Gestión con [uv](https://docs.astral.sh/uv/)** y despliegue vía Docker (multi-stage).

## 2. Estructura del proyecto

```
orchestrator-service/
├── app/
│   ├── main.py            # create_app(): composición de la app FastAPI
│   ├── config.py          # Settings (pydantic-settings) desde variables de entorno
│   ├── dependencies.py    # Wiring de inyección de dependencias (providers)
│   ├── routers/           # Capa de presentación (Controller en MVC)
│   │   └── health.py      # GET /health
│   ├── schemas/           # Modelos Pydantic de request/response (Model en MVC)
│   ├── services/          # Lógica de orquestación (capa de aplicación)
│   └── clients/           # Clientes HTTP hacia cada microservicio
│       └── base_client.py # BaseClient: punto de extensión de resiliencia
├── tests/
│   ├── unit/              # Tests aislados, sin I/O real
│   ├── integration/       # Tests de la app (FastAPI TestClient)
│   └── system/            # Tests de punta a punta contra servicio desplegado
├── main.py                # Entrypoint de desarrollo: uv run python main.py
├── pyproject.toml         # Dependencias + config (ruff, pytest)
├── uv.lock                # Lockfile (builds reproducibles)
├── Dockerfile             # Imagen multi-stage con uv
├── .dockerignore
├── .env.example           # Variables de entorno documentadas
└── README.md
```

## 3. Capas y regla de dependencias

| Capa | Ubicación | Responsabilidad | Depende de |
|---|---|---|---|
| **Presentación** | `app/routers/`, `app/schemas/` | Endpoints HTTP, validación de entrada/salida con Pydantic, errores HTTP | services |
| **Aplicación** | `app/services/` | Orquestación: coordinar llamadas entre microservicios, componer respuestas, manejar fallos | clients |
| **Infraestructura (salida)** | `app/clients/` | Comunicación HTTP con cada microservicio; resiliencia (Retry, Circuit Breaker, Bulkhead) | httpx |

**Regla:** las dependencias apuntan siempre hacia adentro: `router → service → client`.
Está prohibido que `clients/` importe de `services/` o `routers/`, o que un servicio acceda a HTTP directamente sin pasar por un client.

### Diagrama de componentes

```
Cliente ─▶ API Gateway ─▶ ┌──────────────────────────────┐
                          │      orchestrator-service     │
                          │  Routers (FastAPI)            │
                          │       │ Depends               │
                          │       ▼                       │
                          │  Services (orquestación)      │
                          │       │                       │
                          │       ▼                       │
                          │  Clients (httpx)              │
                          └───────┼────────────────────────┘
                                  ▼
              ┌─────────────────────┼─────────────────────┐
           service-a            service-b             service-c
        (resuelve vía Traefik por nombre de servicio)
```

### Diagrama de secuencia (flujo típico)

```
Cliente → Router: request HTTP (validado por schema Pydantic)
Router → Service: llamada a la operación de negocio
Service → Client A: llamada HTTP (async)
Service → Client B: llamada HTTP (async)
Service → Service: agregación de resultados
Router → Cliente: response HTTP (schema de salida)
```

## 4. Componentes clave

### `app/config.py` — Settings
`BaseSettings` (pydantic-settings) lee variables de entorno. `get_settings()` está cacheado con
`lru_cache`. Contiene URLs base de los microservicios (p. ej. `pdf_extract_base_url`) y el timeout HTTP.
**Nunca** valores hardcodeados fuera de aquí.

### `app/dependencies.py` — Inyección de dependencias
Providers con `typing.Annotated[..., Depends(...)]` para usar en los routers
(ej. `SettingsDep`). Acá se cablean también los services y clients.

### `app/clients/base_client.py` — Cliente HTTP base
Wrapper sobre `httpx.AsyncClient` con `base_url` y `timeout`. Cada microservicio coordinado tiene
un cliente concreto que extiende `BaseClient` e implementa solo sus endpoints.
Es el **punto de extensión de resiliencia**: Retry / Circuit Breaker / Bulkhead se aplicarán sobre
esta clase, sin tocar los services.

### `app/services/` — Orquestación
Coordinan uno o más clients, agregan resultados y aplican reglas de composición.
**No debería tener concatenación masiva de llamadas sensibles** ni incluir lógica con estado (no hay estado).

### `app/schemas/` — Modelos
Modelos Pydantic de request/response. Separados por operación o recurso. Nunca se exponen
modelos internos de los microservicios sin traducción.

## 5. Concerns transversales

- **Configuración:** variables de entorno (`.env` en local; env en Docker/orquestador). Secreto: solo vía entorno, nunca en código.
- **Validación:** Pydantic en los schemas (borde de entrada). Los routers no validan a mano.
- **Errores:** los servicios devuelven excepciones de dominio; los routers las traducen a respuestas HTTP
  (esta capa aún sin implementar — agregar con la primera funcionalidad).
- **Observabilidad:** las requests salientes pasan por `BaseClient`: el lugar correcto para logging,
  métricas y resiliencia. **Regla de diseño:** nada de observabilidad ad-hoc por endpoint.
- **Health:** `GET /health` para orquestadores/balanceadores; usado también por el `HEALTHCHECK` del Dockerfile.

## 6. Estrategia de testing

| Suite | Ubicación | Alcance | Herramientas |
|---|---|---|---| 
| Unitarios | `tests/unit/` | Clases/funciones aisladas, sin I/O. HTTP mockeado con **respx** | pytest, pytest-asyncio |
| Integración | `tests/integration/` | App completa en memoria con `TestClient`, valida wiring de routers + DI | httpx TestClient |
| Sistema | `tests/system/` | Punta a punta contra servicio desplegado (Docker/compose); opt-in via `RUN_SYSTEM_TESTS=1` | httpx |

Config: `pyproject.toml` (`testpaths`, `asyncio_mode = "auto"`). Los tests de sistema quedan
excluidos por defecto porque se saltean sin la variable de entorno.

## 7. Despliegue

- **Dockerfile multi-stage:** builder con `ghcr.io/astral-sh/uv` sincroniza deps con `uv sync --locked --no-dev`;
  runtime sobre `python:3.14-alpine` copiando solo `.venv` y `app/`. `--locked` garantiza builds reproducibles.
- `HEALTHCHECK` contra `/health`. Puerto 8000.
- En el entorno real los microservicios se resuelven por **nombre de servicio** detrás de Traefik;
  el orquestador solo conoce URLs inyectadas por entorno.

## 8. Stack tecnológico (deliberadamente mínimo)

| Dependencia | Rol | Por qué sí |
|---|---|---|
| fastapi | Framework HTTP | — |
| uvicorn[standard] | Servidor ASGI | — |
| httpx | Cliente HTTP async a microservicios | — |
| pydantic-settings | Config por entorno | — |

Dev: `pytest`, `pytest-asyncio`, `respx` (mock HTTP), `ruff` (lint: E, F, I, UP, B, ASYNC).
**No hay drivers de base de datos**: este servicio nunca toca una.

## 9. Guía para agregar funcionalidad nueva (blueprint de desarrollo)

Para agregar una operación orquestada (p. ej. "obtener resumen de pedido"):

1. **Cliente** (`app/clients/`): si se llama a un microservicio nuevo, crear/cliente concreto
   extendiendo `BaseClient`. Métodos async que devuelven datos crudos.
2. **Schema** (`app/schemas/`): modelos de request/response públicos.
3. **Servicio** (`app/services/`): función/clase que coordina clients y compone el resultado.
   Recibe clients por constructor/parámetro (DIP).
4. **Router** (`app/routers/`): endpoint fino, valida con schema, delega en el servicio.
5. **Tests**: unitarios del servicio con respx; integración del endpoint; sistema si aplica.
6. Registrar el wiring en `app/dependencies.py` y nuevas URLs en `config.py` + `.env.example`.

### Anti-patrones a evitar

- Routers llamando directamente a `httpx` o a otro microservicio sin service.
- Services con lógica de negocio que pertenece a un microservicio hoja.
- Agregar dependencias de base de datos "temporales" (no hay: el servicio no persiste).
- Instanciar `AsyncClient` por request fuera del ciclo de vida de la app (usar `BaseClient`).

## 10. Decisiones arquitectónicas registradas

| Decisión | Contexto | Consecuencia |
|---|---|---|
| FastAPI + httpx async | Orquestador con muchas llamadas concurrentes en curso | Concurrencia eficiente sin hilos |
| Sin base de datos | El orquestador no tiene estado propio | Simplicidad, hiper-escalable; todo el estado vive en los hojas |
| Resiliencia centralizada en `BaseClient` | Retry/Circuit Breaker uniformes para todos los servicios | Un solo lugar para política de resiliencia |
| `TestClient` para integración | Validar wiring sin servidor real | Tests rápidos deterministas |
| Tests de sistema opt-in (`RUN_SYSTEM_TESTS`) | Requieren servicios reales | CI no falla por entorno no disponible |
| PDF como `bytes` en memoria ([ADR-0001](docs/decisions/0001-representacion-interna-del-pdf.md)) | El hoja espera multipart; máximo 10 MB; sin estado | Sin Base64 ni streams; `PdfDocument` inmutable circula service → client |
