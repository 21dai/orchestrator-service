# orchestrator-service

Microservicio **orquestador** construido con **FastAPI**. Recibe un PDF, lo valida y lo
envía al microservicio de extracción de texto (`pdf-extractext`), devolviendo el
documento procesado. No mantiene estado ni se conecta a bases de datos.

Aplica los patrones de resiliencia **Retry**, **Circuit Breaker** y **Bulkhead** sobre las
llamadas salientes, para que un microservicio lento o caído no arrastre al orquestador.

## Índice

- [Flujo del orquestador](#flujo-del-orquestador)
- [Endpoints](#endpoints)
- [Errores](#errores)
- [Variables de entorno](#variables-de-entorno)
- [Cómo ejecutar](#cómo-ejecutar)
- [Cómo correr los tests](#cómo-correr-los-tests)
- [Docker y Traefik](#docker-y-traefik)
- [Resiliencia](#resiliencia)
- [Arquitectura](#arquitectura)

## Flujo del orquestador

```
Cliente ──▶ Traefik ──▶ orchestrator-service ──▶ pdf-extractext ──▶ MongoDB
                          (sin estado)              (extrae el texto y persiste)
```

Para una request `POST /api/v1/documents`:

```
Router (documents.py)
  │ 1. recibe el multipart (`file`, `name` opcional)
  ▼
pdf_loader.load_pdf()
  │ 2. lee los bytes y valida: extensión .pdf, MIME application/pdf,
  │    no vacío, firma %PDF-  → si falla: 400 (InvalidPdfError)
  │ 3. construye PdfDocument (bytes en memoria, ver ADR-0001)
  ▼
DocumentService.process_pdf()
  │ 4. resuelve el nombre (si no viene, el del archivo sin extensión)
  ▼
PdfExtractClient.create_document()
  │ 5. POST multipart a pdf-extractext, pasando por
  │      Retry → Circuit Breaker → Bulkhead → red
  │ 6. traduce la respuesta (201 → ExtractedDocument) o el fallo
  │    (4xx / 5xx / timeout / sin conexión / circuito abierto / bulkhead lleno)
  │    a excepciones de dominio
  ▼
Router
    7. devuelve 201 con ProcessedDocument; app/errors.py traduce cualquier
       excepción de dominio a un Problem Details con el código HTTP que corresponde
```

Regla de dependencias: **Router → Service → Client**. Ningún router habla HTTP con otro
servicio, ningún service conoce httpx, ningún client importa de las capas de arriba.

## Endpoints

Documentación interactiva (Swagger): `/docs` · OpenAPI: `/openapi.json`.

| Método | Ruta | Descripción | Respuestas |
|---|---|---|---|
| `GET` | `/health` | Salud del servicio (la usan Docker y Traefik) | `200 {"status":"ok"}` |
| `POST` | `/api/v1/documents` | Recibe un PDF, lo procesa vía pdf-extractext y devuelve el texto extraído | `201`, `400`, `422`, `502`, `503`, `504` |

### `POST /api/v1/documents`

Body `multipart/form-data`:

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `file` | archivo | sí | PDF a procesar (`Content-Type: application/pdf`, máximo 10 MB en pdf-extractext) |
| `name` | texto | no | Nombre del documento. Si se omite, se usa el nombre del archivo sin extensión |

```bash
curl.exe -F "name=informe" -F "file=@informe.pdf;type=application/pdf" http://localhost:8000/api/v1/documents
```

Respuesta `201 Created`:

```json
{
  "document_id": 3901,
  "name": "informe",
  "filename": "informe.pdf",
  "size_bytes": 618,
  "checksum": "761a5fd1d83b63282583658a88515dd92b54d4751004e91bfd7e2f1e65b585b3",
  "is_processed": true,
  "extracted_text": "Hola desde el orquestador",
  "processed_at": "2026-09-17T13:50:47.603683Z"
}
```

## Errores

Todos los errores se devuelven como **RFC 9457 Problem Details**
(`Content-Type: application/problem+json`), el mismo formato que usa pdf-extractext:

```json
{
  "type": "about:blank",
  "title": "Service Unavailable",
  "status": 503,
  "detail": "No se pudo conectar con pdf-extractext.",
  "instance": "http://localhost:8000/api/v1/documents"
}
```

| Código | Cuándo | Excepción de dominio |
|---|---|---|
| `400` | El archivo no es un PDF (extensión, MIME, vacío o contenido inválido) | `InvalidPdfError` |
| `400` | pdf-extractext lo rechazó (p. ej. "Ya existe un documento con el mismo checksum"); se reenvía su `detail` | `PdfExtractRejectedError` |
| `422` | Falta el campo `file` u otro error de validación de la request | — |
| `502` | pdf-extractext respondió 5xx o un cuerpo que no se puede interpretar | `PdfExtractUnexpectedResponseError` |
| `503` | No se pudo conectar con pdf-extractext, o la conexión no se estableció a tiempo (tras agotar los reintentos) | `PdfExtractUnavailableError` |
| `503` | Circuit Breaker abierto: pdf-extractext deshabilitado temporalmente por fallos repetidos | `PdfExtractCircuitOpenError` |
| `503` | Bulkhead lleno: demasiadas solicitudes en curso hacia pdf-extractext | `PdfExtractOverloadedError` |
| `504` | pdf-extractext no respondió dentro del timeout | `PdfExtractTimeoutError` |
| `500` | Error no previsto (el `detail` es genérico, no expone internals) | — |

La tabla completa vive en `STATUS_BY_EXCEPTION` en [`app/errors.py`](app/errors.py).

## Variables de entorno

Se leen de `.env` (copiar desde `.env.example`) o del entorno; el entorno tiene prioridad.
Las variables desconocidas se ignoran.

| Variable | Default | Descripción |
|---|---|---|
| `APP_NAME` | `orchestrator-service` | Nombre de la app (título de Swagger) |
| `ENVIRONMENT` | `local` | Entorno de ejecución |
| `PDF_EXTRACT_BASE_URL` | `http://localhost:8000` | URL base de pdf-extractext. En Docker: `http://pdf-extractext:8000` (nombre de servicio) |
| `HTTP_TIMEOUT_SECONDS` | `10` | Timeout de lectura/escritura de cada llamada saliente. Un PDF de 279 páginas tarda ~1,6 s en pdf-extractext |
| `HTTP_CONNECT_TIMEOUT_SECONDS` | `2` | Timeout solo para conectar: más corto, para fallar rápido si pdf-extractext está caído |
| `RETRY_MAX_ATTEMPTS` | `3` | Intentos máximos por llamada, contando el primero |
| `RETRY_BACKOFF_SECONDS` | `0.2` | Base de la espera exponencial entre reintentos |
| `RETRY_MAX_BACKOFF_SECONDS` | `2.0` | Tope de esa espera |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Fallos consecutivos para abrir el circuito |
| `CIRCUIT_BREAKER_RECOVERY_SECONDS` | `30` | Segundos abierto antes de probar una llamada (half-open) |
| `BULKHEAD_MAX_CONCURRENT` | `5` | Requests en vuelo hacia pdf-extractext por proceso |
| `BULKHEAD_MAX_WAITING` | `10` | Requests que pueden esperar un lugar |
| `BULKHEAD_ACQUIRE_TIMEOUT_SECONDS` | `5` | Máximo de espera por un lugar |

## Cómo ejecutar

Requisitos: [uv](https://docs.astral.sh/uv/) (instala Python 3.14 solo).

```bash
uv sync                          # instala dependencias (crea .venv)
cp .env.example .env             # PowerShell: copy .env.example .env
uv run python main.py            # levanta el servicio en :8000 con reload
```

Swagger en `http://localhost:8000/docs`.

Para probar el flujo completo hace falta **pdf-extractext** corriendo. La forma más simple es
levantarlo con su propio compose (desde `../pdf-extractext`, con Docker abierto), que lo expone
en `:8000`; entonces el orquestador tiene que ir en otro puerto:

```bash
uv run uvicorn app.main:app --port 8001 --reload
```

y usar `http://localhost:8001/...` en los ejemplos. `PDF_EXTRACT_BASE_URL` queda en su default.

## Cómo correr los tests

```bash
uv run pytest                    # unitarios + integración; exige cobertura >= 90 %
uv run ruff check . && uv run ruff format --check .   # lint y formato
```

| Suite | Carpeta | Qué prueba | Cómo |
|---|---|---|---|
| Unitarios | `tests/unit/` | Validador, loader, service (con stub del client), clients (HTTP mockeado con **respx**), Retry, Circuit Breaker, Bulkhead, config, wiring | sin I/O real |
| Integración | `tests/integration/` | La app completa en memoria con `TestClient`: endpoints, handlers de error, lifespan; pdf-extractext simulado con respx | sin servicios externos |
| Sistema | `tests/system/` | Punta a punta contra el stack real (Traefik + orquestador + pdf-extractext + MongoDB) | opt-in, ver abajo |

### Tests de sistema (opt-in)

```bash
cd ../infrastructure && docker compose up -d --build   # 1. levantar el stack
```

```bash
# bash
RUN_SYSTEM_TESTS=1 uv run pytest tests/system -o addopts=""
# PowerShell
$env:RUN_SYSTEM_TESTS=1; uv run pytest tests/system -o addopts=""
```

`ORCHESTRATOR_BASE_URL` apunta a otro despliegue (default: `https://orchestrator.proyecto.localhost`).
`-o addopts=""` evita que el umbral de cobertura de la suite regular aplique a estos tests.

## Docker y Traefik

### Imagen del orquestador

Dockerfile multi-stage con `uv`; imagen final sobre `python:3.14-alpine`, puerto `8000`,
`HEALTHCHECK` contra `/health`.

```bash
docker build -t orchestrator-service .
docker run --rm -p 8000:8000 -e PDF_EXTRACT_BASE_URL=http://host.docker.internal:8000 orchestrator-service
```

### Stack completo con Traefik

El despliegue completo (Traefik como reverse proxy y balanceador, orquestador con réplicas,
pdf-extractext y MongoDB, todo en una red Docker) vive en la carpeta hermana
**`../infrastructure`**, con su propio README. Resumen:

```bash
cd ../infrastructure
mkcert -install                                                  # una sola vez
mkcert -cert-file certs/local-cert.pem -key-file certs/local-key.pem "*.proyecto.localhost" "proyecto.localhost" localhost 127.0.0.1 ::1
docker compose up -d --build                                     # levantar todo
docker compose up -d --build --scale orchestrator=3              # con 3 réplicas del orquestador
docker compose down                                              # apagar
```

| Qué | Dónde |
|---|---|
| Orquestador (vía Traefik, HTTPS) | `https://orchestrator.proyecto.localhost` |
| Dashboard de Traefik | `http://localhost:8080/dashboard/` |

Dentro de la red, el orquestador llama a pdf-extractext por nombre de servicio
(`PDF_EXTRACT_BASE_URL=http://pdf-extractext:8000`, definido en el compose). Los contenedores
no publican puertos al host: solo Traefik expone 80/443. Traefik descubre las réplicas por
los labels del compose y balancea entre ellas.

## Resiliencia

Los tres patrones viven en `app/clients/` y se aplican en `BaseClient._request`, el único
punto por donde salen requests. Los services no saben que existen. El detalle y las razones
(basadas en mediciones de carga sobre pdf-extractext) están en
[ADR-0002](docs/decisions/0002-politica-de-resiliencia.md).

| Patrón | Módulo | Qué hace | Por qué así |
|---|---|---|---|
| **Retry** | `retry.py` | Reintenta solo fallos de conexión y 5xx en métodos idempotentes; backoff exponencial con jitter | Un POST que agota el timeout **igual crea el documento** en pdf-extractext: reintentarlo duplica trabajo y termina en 400 |
| **Circuit Breaker** | `circuit_breaker.py` | Tras N fallos consecutivos (transporte, timeout, 5xx) rechaza en el acto; pasado el tiempo de recuperación deja pasar una llamada de prueba | Seguir enviando PDFs a un hoja saturado solo agranda su cola |
| **Bulkhead** | `bulkhead.py` | Acota las requests en vuelo y en espera hacia pdf-extractext; el exceso recibe 503 inmediato | Cada request retiene un PDF en memoria; sin tope, un hoja lento agota la memoria del orquestador |

Orden por cada intento: `Retry → Circuit Breaker → Bulkhead → red`. Si el circuito está abierto
no se ocupa un lugar en el Bulkhead ni se reintenta.

## Arquitectura

Arquitectura de 3 capas con patrón MVC:

| Capa | Ubicación | Responsabilidad |
|---|---|---|
| Presentación | `app/routers/`, `app/schemas/`, `app/errors.py` | HTTP, validación de entrada (Pydantic), traducción de excepciones a Problem Details |
| Aplicación | `app/services/` | Validación del PDF, conversión a `PdfDocument`, orquestación |
| Infraestructura de salida | `app/clients/` | Clients HTTP (httpx) hacia cada microservicio; Retry, Circuit Breaker, Bulkhead |

```
app/
├── main.py                 # create_app() + lifespan (crea/cierra los clients)
├── config.py               # Settings (pydantic-settings)
├── dependencies.py         # wiring: client singleton, service, políticas de resiliencia
├── errors.py               # excepciones de dominio → Problem Details
├── exceptions.py           # excepciones de dominio
├── routers/   documents.py, health.py
├── schemas/   documents.py (PdfDocument, ProcessedDocument), pdf_extract.py (contrato del hoja)
├── services/  document_service.py, pdf_loader.py, pdf_validator.py
└── clients/   base_client.py, pdf_extract_client.py, retry.py, circuit_breaker.py, bulkhead.py
```

- Detalle de capas, componentes y guía para agregar funcionalidad:
  [`Project_Architecture_Blueprint.md`](Project_Architecture_Blueprint.md).
- Decisiones de diseño (ADRs): [`docs/decisions/`](docs/decisions/).
- Contrato de pdf-extractext que consume el orquestador: `POST /api/v1/documents`
  (multipart `name` + `file`) → `201 DocumentResponse`; errores en Problem Details.
