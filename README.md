# orchestrator-service

Microservicio **orquestador** construido con **FastAPI**. Recibe un PDF, lo valida, le pide
el texto a `pdf-extractext` y guarda el resultado en `document-store`, devolviendo el
documento guardado. No mantiene estado ni se conecta a bases de datos: extraer es trabajo
de pdf-extractext y persistir, de document-store.

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
                                         ┌──▶ pdf-extractext   (POST /extract: texto y páginas, sin estado)
Cliente ──▶ Traefik ──▶ orchestrator-service
                          (sin estado)   └──▶ document-store ──▶ MongoDB   (guarda el documento)
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
  │ 4. resuelve el nombre (si no viene, el del archivo sin extensión;
  │    más de 255 caracteres → 400 sin llamar a nadie)
  ▼
PdfExtractClient.extract()
  │ 5. POST /extract (multipart con el PDF) → texto en Markdown y cantidad de páginas.
  │    Si falla, no se guarda nada.
  ▼
DocumentService
  │ 6. arma el documento: SHA-256 de los bytes del PDF, tamaño, nombre, texto, páginas
  ▼
DocumentStoreClient.create_document()
  │ 7. POST /api/v1/documents (JSON) → el documento guardado con su id.
  │    Si el checksum ya existía, document-store responde 409.
  ▼
Router
    8. devuelve 201 con ProcessedDocument; app/errors.py traduce cualquier
       excepción de dominio a un Problem Details con el código HTTP que corresponde
```

Cada llamada sale por `Retry → Circuit Breaker → Bulkhead → red`, con un breaker y un
bulkhead **por microservicio**: si document-store se cae, la extracción no se corta, y al
revés. Cada client traduce las respuestas y los fallos de su servicio (4xx, 5xx, timeout,
sin conexión, circuito abierto, bulkhead lleno) a excepciones de dominio.

No hace falta una Saga: `/extract` no guarda nada, así que si falla el guardado no queda
nada a medias que compensar. El flujo y sus decisiones están en
[ADR-0003](docs/decisions/0003-coordinar-extraccion-y-persistencia.md).

Regla de dependencias: **Router → Service → Client**. Ningún router habla HTTP con otro
servicio, ningún service conoce httpx, ningún client importa de las capas de arriba.

## Endpoints

Documentación interactiva (Swagger): `/docs` · OpenAPI: `/openapi.json`.

| Método | Ruta | Descripción | Respuestas |
|---|---|---|---|
| `GET` | `/health` | Salud del servicio (la usan Docker y Traefik) | `200 {"status":"ok"}` |
| `POST` | `/api/v1/documents` | Recibe un PDF, extrae su texto con pdf-extractext, lo guarda en document-store y devuelve el documento guardado | `201`, `400`, `409`, `413`, `422`, `502`, `503`, `504` |

### `POST /api/v1/documents`

Body `multipart/form-data`:

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `file` | archivo | sí | PDF a procesar (`Content-Type: application/pdf`, máximo 10 MB: `MAX_PDF_SIZE_BYTES`). Se lee en memoria y nunca se escribe a disco |
| `name` | texto | no | Nombre del documento, hasta 255 caracteres. Si se omite, se usa el nombre del archivo sin extensión |

```bash
curl.exe -F "name=informe" -F "file=@informe.pdf;type=application/pdf" http://localhost:8000/api/v1/documents
```

Respuesta `201 Created`:

```json
{
  "document_id": 1,
  "name": "informe",
  "filename": "informe.pdf",
  "size_bytes": 618,
  "checksum": "761a5fd1d83b63282583658a88515dd92b54d4751004e91bfd7e2f1e65b585b3",
  "page_count": 1,
  "is_processed": true,
  "extracted_text": "Hola desde el orquestador",
  "processed_at": "2026-10-23T13:50:47.603Z"
}
```

`document_id` es el id que asigna document-store; `checksum` es el SHA-256 de los bytes del
PDF, calculado por el orquestador.

## Errores

Todos los errores se devuelven como **RFC 9457 Problem Details**
(`Content-Type: application/problem+json`), el mismo formato que usan pdf-extractext y
document-store:

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
| `400` | El archivo no es un PDF (extensión, MIME, vacío o contenido inválido) o su nombre supera los 255 caracteres | `InvalidPdfError` |
| `400` | El `name` supera los 255 caracteres | `InvalidDocumentNameError` |
| `400` | pdf-extractext no pudo leer el PDF (p. ej. un PDF dañado); se reenvía su `detail` | `PdfExtractRejectedError` |
| `409` | El PDF ya estaba guardado: document-store tiene un documento con el mismo checksum | `DocumentAlreadyExistsError` |
| `413` | El PDF supera `MAX_PDF_SIZE_BYTES` (10 MB). Se corta sin terminar de leerlo | `PdfTooLargeError` |
| `422` | Falta el campo `file` u otro error de validación de la request | — |
| `502` | pdf-extractext o document-store respondió 5xx o un cuerpo que no se puede interpretar | `PdfExtractUnexpectedResponseError`, `DocumentStoreUnexpectedResponseError` |
| `502` | document-store rechazó los datos con otro 4xx: el orquestador ya los validó, así que es un contrato roto o una URL mal configurada | `DocumentStoreRejectedError` |
| `503` | No se pudo conectar con el servicio (tras agotar los reintentos), no quedan réplicas sanas (503 de Traefik) o document-store no tiene MongoDB (503 sin `Retry-After`) | `PdfExtractUnavailableError`, `DocumentStoreUnavailableError` |
| `503` + `Retry-After` | Circuit Breaker abierto: el servicio está deshabilitado temporalmente por fallos repetidos | `PdfExtractCircuitOpenError`, `DocumentStoreCircuitOpenError` |
| `503` | Bulkhead lleno: demasiadas solicitudes en curso hacia ese servicio | `PdfExtractOverloadedError`, `DocumentStoreOverloadedError` |
| `503` + `Retry-After` | El servicio tiene la cola llena (503 con `Retry-After`, como pdf-extractext): se reenvía su `Retry-After` | `PdfExtractOverloadedError`, `DocumentStoreOverloadedError` |
| `504` | El servicio no respondió dentro de su timeout | `PdfExtractTimeoutError`, `DocumentStoreTimeoutError` |
| `500` | Error no previsto (el `detail` es genérico, no expone internals) | — |

El `detail` de los errores de los servicios nombra cuál falló (`pdf-extractext` o
`document-store`). Si falla la extracción, el orquestador no llama a document-store.

La tabla completa vive en `STATUS_BY_EXCEPTION` en [`app/errors.py`](app/errors.py).

## Variables de entorno

Se leen de `.env` (copiar desde `.env.example`) o del entorno; el entorno tiene prioridad.
Las variables desconocidas se ignoran.

| Variable | Default | Descripción |
|---|---|---|
| `APP_NAME` | `orchestrator-service` | Nombre de la app (título de Swagger) |
| `ENVIRONMENT` | `local` | Entorno de ejecución |
| `PDF_EXTRACT_BASE_URL` | `http://localhost:8000` | URL base de pdf-extractext. En Docker: `http://pdf-extractext:8000` (nombre de servicio) |
| `DOCUMENT_STORE_BASE_URL` | `http://localhost:8002` | URL base de document-store. En Docker: `http://document-store:8000` |
| `PDF_EXTRACT_TIMEOUT_SECONDS` | `30` | Timeout de lectura/escritura hacia pdf-extractext. Mayor que los 25 s en los que pdf-extractext responde 503 si no llega a extraer (`EXTRACT_MAX_WAIT_SECONDS`), para recibir ese 503 en vez de cortar una extracción que iba a terminar. Acepta también el nombre anterior, `HTTP_TIMEOUT_SECONDS` |
| `DOCUMENT_STORE_TIMEOUT_SECONDS` | `5` | Timeout de lectura/escritura hacia document-store, que solo guarda un JSON |
| `HTTP_CONNECT_TIMEOUT_SECONDS` | `2` | Timeout solo para conectar, el mismo para los dos: más corto, para fallar rápido si un servicio está caído |
| `RETRY_MAX_ATTEMPTS` | `3` | Intentos máximos por llamada, contando el primero |
| `RETRY_BACKOFF_SECONDS` | `0.2` | Base de la espera exponencial entre reintentos |
| `RETRY_MAX_BACKOFF_SECONDS` | `2.0` | Tope de esa espera |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Fallos consecutivos para abrir el circuito |
| `CIRCUIT_BREAKER_RECOVERY_SECONDS` | `30` | Segundos abierto antes de probar una llamada (half-open) |
| `BULKHEAD_MAX_CONCURRENT` | `5` | Requests en vuelo hacia cada microservicio por proceso (un bulkhead por servicio) |
| `BULKHEAD_MAX_WAITING` | `10` | Requests que pueden esperar un lugar |
| `BULKHEAD_ACQUIRE_TIMEOUT_SECONDS` | `5` | Máximo de espera por un lugar |
| `MAX_PDF_SIZE_BYTES` | `10485760` | Tamaño máximo del PDF (10 MB, igual que pdf-extractext). Más grande: `413` |

## Cómo ejecutar

Requisitos: [uv](https://docs.astral.sh/uv/) (instala Python 3.14 solo).

```bash
uv sync                          # instala dependencias (crea .venv)
cp .env.example .env             # PowerShell: copy .env.example .env
uv run python main.py            # levanta el servicio en :8000 con reload
```

Swagger en `http://localhost:8000/docs`.

Para probar el flujo completo hacen falta **pdf-extractext** y **document-store** corriendo.
Con los defaults, cada uno va en su puerto:

| Servicio | Puerto local | Cómo |
|---|---|---|
| pdf-extractext | `:8000` | su propio compose, desde `../pdf-extractext` (con Docker abierto) |
| orquestador | `:8001` | `uv run uvicorn app.main:app --port 8001 --reload` |
| document-store | `:8002` | desde `../document-store`, con su MongoDB (ver su README) y `PORT=8002` |

Usar `http://localhost:8001/...` en los ejemplos. `PDF_EXTRACT_BASE_URL` y
`DOCUMENT_STORE_BASE_URL` quedan en sus defaults.

## Cómo correr los tests

```bash
uv run pytest                    # unitarios + integración; exige cobertura >= 90 %
uv run ruff check . && uv run ruff format --check .   # lint y formato
```

| Suite | Carpeta | Qué prueba | Cómo |
|---|---|---|---|
| Unitarios | `tests/unit/` | Validador, loader, service (con stubs de los dos clients), clients (HTTP mockeado con **respx**), Retry, Circuit Breaker, Bulkhead, config, wiring | sin I/O real |
| Integración | `tests/integration/` | La app completa en memoria con `TestClient`: endpoints, handlers de error, lifespan; pdf-extractext y document-store simulados con respx | sin servicios externos |
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
docker run --rm -p 8001:8000 -e PDF_EXTRACT_BASE_URL=http://host.docker.internal:8000 -e DOCUMENT_STORE_BASE_URL=http://host.docker.internal:8002 orchestrator-service
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

Dentro de la red, el orquestador llama a cada servicio por su nombre
(`PDF_EXTRACT_BASE_URL=http://pdf-extractext:8000` y
`DOCUMENT_STORE_BASE_URL=http://document-store:8000`, definidos en el compose). Los contenedores
no publican puertos al host: solo Traefik expone 80/443. Traefik descubre las réplicas por
los labels del compose y balancea entre ellas.

## Resiliencia

Los tres patrones viven en `app/clients/` y se aplican en `BaseClient._request`, el único
punto por donde salen requests. Los services no saben que existen. Cada microservicio tiene
su propio Circuit Breaker y Bulkhead. El detalle y las razones (basadas en mediciones de
carga sobre pdf-extractext) están en [ADR-0002](docs/decisions/0002-politica-de-resiliencia.md).

| Patrón | Módulo | Qué hace | Por qué así |
|---|---|---|---|
| **Retry** | `retry.py` | Reintenta solo fallos de conexión y 5xx en métodos idempotentes; backoff exponencial con jitter | Un POST que agota el timeout puede haberse procesado igual: reintentarlo duplica la extracción o termina en 409 en document-store |
| **Circuit Breaker** | `circuit_breaker.py` | Tras N fallos consecutivos (transporte, timeout, 5xx) rechaza en el acto; pasado el tiempo de recuperación deja pasar una llamada de prueba. Un 503 con `Retry-After` (cola llena del hoja) no cuenta como fallo | Seguir enviando PDFs a un hoja caído solo agranda su cola; uno sano pero ocupado ya se defiende solo |
| **Bulkhead** | `bulkhead.py` | Acota las requests en vuelo y en espera hacia cada microservicio; el exceso recibe 503 inmediato | Cada request retiene un PDF en memoria; sin tope, un hoja lento agota la memoria del orquestador |

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
├── dependencies.py         # wiring: un client singleton por servicio, service, políticas de resiliencia
├── errors.py               # excepciones de dominio → Problem Details
├── exceptions.py           # excepciones de dominio
├── routers/   documents.py, health.py
├── schemas/   documents.py (PdfDocument, ProcessedDocument), pdf_extract.py y document_store.py (contratos de los servicios)
├── services/  document_service.py, pdf_loader.py, pdf_validator.py
└── clients/   base_client.py, pdf_extract_client.py, document_store_client.py, retry.py, circuit_breaker.py, bulkhead.py
```

- Detalle de capas, componentes y guía para agregar funcionalidad:
  [`Project_Architecture_Blueprint.md`](Project_Architecture_Blueprint.md).
- Decisiones de diseño (ADRs): [`docs/decisions/`](docs/decisions/).
- Contratos que consume el orquestador, con errores en Problem Details:
  - pdf-extractext: `POST /extract` (multipart `file`) → `200 {"content", "page_count"}`.
  - document-store: `POST /api/v1/documents` (JSON con `name`, `original_filename`,
    `checksum`, `file_size`, `page_count`, `extracted_text`, `is_processed`) →
    `201` con el documento y su `id`; `409` si el checksum ya existe.
