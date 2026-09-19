# Project Architecture Blueprint — orchestrator-service

> Fecha de generación: 2026-09-11 · Última actualización: 2026-09-19 · Estado: flujo de PDF
> completo con Retry, Circuit Breaker y Bulkhead. Regenerar cuando la arquitectura evolucione.

## 1. Visión general

Microservicio **orquestador** construido con **FastAPI**. Recibe un PDF a través de Traefik, lo
valida, lo envía al microservicio de extracción de texto (`pdf-extractext`) y devuelve el documento
procesado. Está diseñado para coordinar más microservicios con el mismo esquema.

Principios rectores:

- **Sin estado, sin base de datos.** El servicio no persiste nada; toda la información proviene de los microservicios que coordina.
- **Arquitectura de 3 capas + MVC** con regla de dependencias unidireccional **Router → Service → Client**.
- **SOLID y Clean Code**: responsabilidad única por módulo, extensión sin modificación (OCP), inversión de dependencias vía `fastapi.Depends`.
- **Gestión con [uv](https://docs.astral.sh/uv/)** y despliegue vía Docker (multi-stage).

## 2. Estructura del proyecto

```
orchestrator-service/
├── app/
│   ├── main.py                  # create_app() + lifespan (crea/cierra los clients)
│   ├── config.py                # Settings (pydantic-settings) desde variables de entorno
│   ├── dependencies.py          # Wiring de inyección de dependencias (providers)
│   ├── exceptions.py            # Excepciones de dominio (InvalidPdfError, PdfExtract*Error)
│   ├── errors.py                # Excepciones → respuestas HTTP RFC 9457 Problem Details
│   ├── routers/                 # Capa de presentación (Controller en MVC)
│   │   ├── health.py            # GET /health
│   │   └── documents.py         # POST /api/v1/documents
│   ├── schemas/                 # Modelos (Model en MVC)
│   │   ├── documents.py         # PdfDocument (interno), ProcessedDocument (respuesta pública)
│   │   └── pdf_extract.py       # ExtractedDocument: contrato de pdf-extractext
│   ├── services/                # Capa de aplicación
│   │   ├── pdf_validator.py     # validate_pdf(): extensión, MIME, firma %PDF-
│   │   ├── pdf_loader.py        # load_pdf(): UploadFile → PdfDocument
│   │   └── document_service.py  # DocumentService.process_pdf(): orquestación
│   └── clients/                 # Infraestructura de salida
│       ├── base_client.py       # BaseClient: _request = Retry → Circuit Breaker → Bulkhead → red
│       ├── pdf_extract_client.py# PdfExtractClient.create_document()
│       ├── retry.py             # RetryPolicy
│       ├── circuit_breaker.py   # CircuitBreaker (cerrado/abierto/half-open)
│       └── bulkhead.py          # Bulkhead (en vuelo + cola acotada)
├── docs/decisions/              # ADRs (0001 representación del PDF, 0002 resiliencia)
├── tests/
│   ├── unit/                    # Tests aislados, sin I/O real (respx para HTTP)
│   ├── integration/             # Tests de la app (FastAPI TestClient)
│   └── system/                  # Punta a punta contra el stack desplegado (opt-in)
├── main.py                      # Entrypoint de desarrollo: uv run python main.py
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
| **Presentación** | `app/routers/`, `app/schemas/`, `app/errors.py` | Endpoints HTTP, validación de entrada/salida con Pydantic, traducción de excepciones a Problem Details | services |
| **Aplicación** | `app/services/` | Validación y conversión del PDF, orquestación: coordinar clients y componer la respuesta | clients (vía `Protocol`) |
| **Infraestructura (salida)** | `app/clients/` | Comunicación HTTP con cada microservicio; resiliencia (Retry, Circuit Breaker, Bulkhead) | httpx |

**Regla:** las dependencias apuntan siempre hacia adentro: `router → service → client`.
Está prohibido que `clients/` importe de `services/` o `routers/`, o que un servicio acceda a HTTP directamente sin pasar por un client.

### Diagrama de componentes

```
Cliente ─▶ Traefik ─▶ ┌───────────────────────────────────────────┐
         (réplicas)   │            orchestrator-service            │
                      │  Routers (FastAPI)  ◀── errors.py          │
                      │       │ Depends                            │
                      │       ▼                                    │
                      │  Services (validar, convertir, orquestar)  │
                      │       │                                    │
                      │       ▼                                    │
                      │  Clients: Retry → Circuit Breaker →        │
                      │           Bulkhead → httpx                 │
                      └───────┼────────────────────────────────────┘
                              ▼
                        pdf-extractext ─▶ MongoDB
              (resuelve por nombre de servicio en la red Docker)
```

### Diagrama de secuencia (`POST /api/v1/documents`)

```
Cliente → Router: multipart (file, name?)
Router → pdf_loader.load_pdf: lee bytes, validate_pdf(), arma PdfDocument   [400 si no es PDF]
Router → DocumentService.process_pdf(document, name)
Service → PdfExtractClient.create_document(document, name)
Client  → pdf-extractext: POST /api/v1/documents (multipart)   [Retry / CB / Bulkhead]
Client  → Service: ExtractedDocument   | excepción PdfExtract*Error
Service → Router: ProcessedDocument
Router  → Cliente: 201                 | errors.py → Problem Details 400/502/503/504
```

## 4. Componentes clave

### `app/config.py` — Settings
`BaseSettings` (pydantic-settings) lee variables de entorno. `get_settings()` está cacheado con
`lru_cache`. Contiene URLs base de los microservicios (p. ej. `pdf_extract_base_url`), el timeout HTTP
y los parámetros de Retry, Circuit Breaker y Bulkhead. `extra="ignore"`: un `.env` compartido con
variables de otros servicios no rompe el arranque. **Nunca** valores hardcodeados fuera de aquí.

### `app/dependencies.py` — Inyección de dependencias
Providers con `typing.Annotated[..., Depends(...)]` para usar en los routers
(ej. `DocumentServiceDep`). `get_pdf_extract_client()` es un singleton por proceso (`lru_cache`)
que **se crea en el lifespan al arrancar** y se cierra al apagar: los patrones de resiliencia
tienen estado y deben ser uno solo por microservicio. Acá se construyen también las políticas
(`build_retry_policy`, `build_circuit_breaker`, `build_bulkhead`) a partir de `Settings`.

### `app/clients/base_client.py` — Cliente HTTP base
Wrapper sobre `httpx.AsyncClient` con `base_url` y `timeout`. Cada microservicio coordinado tiene
un cliente concreto que extiende `BaseClient` e implementa solo sus endpoints, siempre vía
`_request`, el **único punto de salida**: `_request` = Retry → `_send` = Circuit Breaker → Bulkhead →
red. Los errores de httpx se propagan crudos; cada cliente concreto (p. ej. `PdfExtractClient`) los
traduce a excepciones de dominio junto con las respuestas de error del hoja. Política y valores:
[ADR-0002](docs/decisions/0002-politica-de-resiliencia.md).

### `app/services/` — Orquestación
Coordinan uno o más clients, agregan resultados y aplican reglas de composición. Reciben los
clients por constructor contra un `Protocol` (`PdfExtractGateway`), así se prueban con un stub.
`pdf_validator.py` y `pdf_loader.py` son el borde de entrada del PDF: validan y convierten el
`UploadFile` a `PdfDocument` fuera del router. **No debería tener concatenación masiva de llamadas
sensibles** ni incluir lógica con estado (no hay estado).

### `app/schemas/` — Modelos
Modelos Pydantic de request/response y objetos que cruzan capas. `PdfDocument` (dataclass
inmutable, ADR-0001) vive acá porque lo usan services **y** clients, y `clients/` no puede importar
de `services/`. `pdf_extract.py` tipa el contrato del hoja; nunca se expone tal cual: `ProcessedDocument`
lo traduce a los nombres públicos del orquestador.

## 5. Concerns transversales

- **Configuración:** variables de entorno (`.env` en local; env en Docker/orquestador). Secreto: solo vía entorno, nunca en código.
- **Validación:** Pydantic en los schemas (borde de entrada). Los routers no validan a mano.
- **Errores:** services y clients lanzan excepciones de dominio (`app/exceptions.py`); nadie las captura a
  mano en los routers. `app/errors.py` registra un handler por excepción (`STATUS_BY_EXCEPTION`) y los
  handlers de validación (422), `HTTPException` y fallback (500), todos en formato RFC 9457 Problem Details,
  el mismo que usa pdf-extractext. Los fallos del hoja usan códigos de gateway (502/503/504).
- **Resiliencia:** Retry, Circuit Breaker y Bulkhead en `app/clients/`, aplicados en `BaseClient`; los
  services no saben que existen. Parámetros por entorno. Ver [ADR-0002](docs/decisions/0002-politica-de-resiliencia.md).
- **Observabilidad:** las requests salientes pasan por `BaseClient`: el lugar correcto para logging,
  métricas y resiliencia. **Regla de diseño:** nada de observabilidad ad-hoc por endpoint.
- **Health:** `GET /health` para orquestadores/balanceadores; usado también por el `HEALTHCHECK` del Dockerfile.

## 6. Estrategia de testing

| Suite | Ubicación | Alcance | Herramientas |
|---|---|---|---| 
| Unitarios | `tests/unit/` | Clases/funciones aisladas, sin I/O. HTTP mockeado con **respx** | pytest, pytest-asyncio |
| Integración | `tests/integration/` | App completa en memoria con `TestClient`, valida wiring de routers + DI | httpx TestClient |
| Sistema | `tests/system/` | Punta a punta contra servicio desplegado (Docker/compose); opt-in via `RUN_SYSTEM_TESTS=1` | httpx |

Config: `pyproject.toml` (`testpaths`, `asyncio_mode = "auto"`, cobertura mínima 90 % con
`pytest-cov`). Los tests de sistema quedan excluidos por defecto porque se saltean sin la variable de
entorno y se corren con `-o addopts=""` para no aplicarles el umbral de cobertura. Los patrones de
resiliencia se prueban sin dormir: `RetryPolicy` recibe `sleep`, `CircuitBreaker` recibe `clock`.

## 7. Despliegue

- **Dockerfile multi-stage:** builder con `ghcr.io/astral-sh/uv` sincroniza deps con `uv sync --locked --no-dev`;
  runtime sobre `python:3.14-alpine` copiando solo `.venv` y `app/`. `--locked` garantiza builds reproducibles.
- `HEALTHCHECK` contra `/health`. Puerto 8000.
- El stack completo (Traefik + réplicas del orquestador + pdf-extractext + MongoDB) se levanta desde la
  carpeta hermana `../infrastructure` (`docker compose up -d --build`, `--scale orchestrator=N`).
  Traefik expone `https://orchestrator.proyecto.localhost` y balancea entre réplicas.
- Los microservicios se resuelven por **nombre de servicio** en la red Docker
  (`PDF_EXTRACT_BASE_URL=http://pdf-extractext:8000`); el orquestador solo conoce URLs inyectadas por entorno.
- Cada réplica tiene su propio estado de Retry/Circuit Breaker/Bulkhead (sin estado compartido, por diseño).

## 8. Stack tecnológico (deliberadamente mínimo)

| Dependencia | Rol | Por qué sí |
|---|---|---|
| fastapi | Framework HTTP | — |
| uvicorn[standard] | Servidor ASGI | — |
| httpx | Cliente HTTP async a microservicios | — |
| pydantic-settings | Config por entorno | — |
| python-multipart | Parseo de `multipart/form-data` (lo exige FastAPI para `UploadFile`) | — |

Dev: `pytest`, `pytest-asyncio`, `pytest-cov`, `respx` (mock HTTP), `ruff` (lint: E, F, I, UP, B, ASYNC).
Resiliencia implementada a mano (sin tenacity/aiobreaker): ver ADR-0002.
**No hay drivers de base de datos**: este servicio nunca toca una.

## 9. Guía para agregar funcionalidad nueva (blueprint de desarrollo)

Para agregar una operación orquestada (p. ej. "obtener resumen de pedido"):

1. **Cliente** (`app/clients/`): si se llama a un microservicio nuevo, crear un cliente concreto
   extendiendo `BaseClient`, que use `_request` y traduzca errores HTTP/transporte a excepciones de
   dominio (ver `pdf_extract_client.py` como modelo). Retry/CB/Bulkhead vienen gratis.
2. **Excepciones** (`app/exceptions.py`) y su código HTTP en `STATUS_BY_EXCEPTION` (`app/errors.py`).
3. **Schema** (`app/schemas/`): contrato del hoja (interno) y modelo de respuesta público (traducido).
4. **Servicio** (`app/services/`): función/clase que coordina clients y compone el resultado.
   Recibe clients por constructor contra un `Protocol` (DIP).
5. **Router** (`app/routers/`): endpoint fino, valida con schema, delega en el servicio.
6. **Tests**: unitarios del client con respx y del servicio con un stub; integración del endpoint;
   sistema si aplica.
7. Registrar el wiring en `app/dependencies.py` (singleton creado en el lifespan) y nuevas URLs en
   `config.py` + `.env.example`; documentar el endpoint en el README.

### Anti-patrones a evitar

- Routers llamando directamente a `httpx` o a otro microservicio sin service.
- Services con lógica de negocio que pertenece a un microservicio hoja.
- Agregar dependencias de base de datos "temporales" (no hay: el servicio no persiste).
- Instanciar `AsyncClient` por request fuera del ciclo de vida de la app (usar `BaseClient`).
- Crear el client perezosamente en la primera request: varias requests simultáneas construirían cada
  una el suyo (`lru_cache` no es atómico entre hilos) y los patrones no compartirían estado.
- Capturar excepciones de dominio en un router para armar la respuesta a mano (va en `errors.py`).
- Reintentar timeouts de `POST` hacia pdf-extractext (el documento se crea igual; ver ADR-0002).

## 10. Decisiones arquitectónicas registradas

| Decisión | Contexto | Consecuencia |
|---|---|---|
| FastAPI + httpx async | Orquestador con muchas llamadas concurrentes en curso | Concurrencia eficiente sin hilos |
| Sin base de datos | El orquestador no tiene estado propio | Simplicidad, hiper-escalable; todo el estado vive en los hojas |
| Resiliencia centralizada en `BaseClient` | Retry/Circuit Breaker uniformes para todos los servicios | Un solo lugar para política de resiliencia |
| `TestClient` para integración | Validar wiring sin servidor real | Tests rápidos deterministas |
| Tests de sistema opt-in (`RUN_SYSTEM_TESTS`) | Requieren servicios reales | CI no falla por entorno no disponible |
| PDF como `bytes` en memoria ([ADR-0001](docs/decisions/0001-representacion-interna-del-pdf.md)) | El hoja espera multipart; máximo 10 MB; sin estado | Sin Base64 ni streams; `PdfDocument` inmutable circula service → client |
| Errores en RFC 9457 Problem Details | pdf-extractext ya usa ese formato | Un solo formato de error en todo el sistema; handlers centralizados en `errors.py` |
| Retry solo de fallos de conexión, nunca timeouts de POST ([ADR-0002](docs/decisions/0002-politica-de-resiliencia.md)) | El hoja procesa igual una request cuyo cliente agotó el timeout | No se duplica trabajo ni se agranda la cola del hoja |
| Circuit Breaker por microservicio, en proceso ([ADR-0002](docs/decisions/0002-politica-de-resiliencia.md)) | Timeouts consecutivos = hoja saturado | Deja de alimentar la cola del hoja; sin estado compartido entre réplicas |
| Bulkhead por microservicio ([ADR-0002](docs/decisions/0002-politica-de-resiliencia.md)) | Cada request retiene un PDF en memoria | Memoria y espera acotadas; el exceso recibe 503 inmediato |
| Clients creados en el lifespan | `lru_cache` no es atómico ante requests simultáneas | Un solo client (y un solo estado de resiliencia) por proceso |
