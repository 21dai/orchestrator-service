# orchestrator-service

Microservicio **orquestador** construido con **FastAPI**. Recibe un PDF, lo valida, le pide
el texto a `pdf-extractext` y guarda el resultado en `document-store`, devolviendo el
documento guardado. También expone el CRUD de los documentos guardados (listar, ver,
renombrar y borrar), delegando en document-store, que no se publica por Traefik: el orquestador
es el único punto de entrada. No mantiene estado ni se conecta a bases de datos: extraer es
trabajo de pdf-extractext y persistir, de document-store.

Aplica los patrones de resiliencia **Retry** y **Bulkhead** sobre las llamadas salientes, que
en el stack pasan por el entrypoint interno de Traefik, donde está el **Circuit Breaker**
([ADR-0005](docs/decisions/0005-circuit-breaker-en-traefik.md)). Así un microservicio lento o
caído no arrastra al orquestador.

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

Cada llamada sale por `Retry → Bulkhead → red`, con un bulkhead **por microservicio**: si
document-store se satura, no ocupa los lugares de la extracción, y al revés. En el stack, la
red es el entrypoint interno de Traefik (`http://traefik:8081`), que balancea entre las réplicas
y aplica un circuit breaker por servicio. Cada client traduce las respuestas y los fallos de su
servicio (4xx, 5xx, timeout, sin conexión, circuito abierto en Traefik, bulkhead lleno) a
excepciones de dominio.

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
| `GET` | `/api/v1/documents?skip=0&limit=10` | Lista los documentos guardados, en orden de id ascendente | `200`, `422`, `502`, `503`, `504` |
| `GET` | `/api/v1/documents/{document_id}` | Devuelve un documento con su texto extraído | `200`, `404`, `422`, `502`, `503`, `504` |
| `PUT` | `/api/v1/documents/{document_id}` | Renombra un documento (`{"name": "..."}`, lo único editable) | `200`, `404`, `422`, `502`, `503`, `504` |
| `DELETE` | `/api/v1/documents/{document_id}` | Borra un documento | `204`, `404`, `422`, `502`, `503`, `504` |

Todas las respuestas usan el mismo modelo, `ProcessedDocument`; los errores, Problem Details
(ver [Errores](#errores)). Los ejemplos van contra el stack con Traefik (`-k`: certificado local
de mkcert); en local, cambiar la URL por `http://localhost:8000`.

### `POST /api/v1/documents`

Body `multipart/form-data`:

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `file` | archivo | sí | PDF a procesar (`Content-Type: application/pdf`, máximo 10 MB: `MAX_PDF_SIZE_BYTES`). Se lee en memoria y nunca se escribe a disco |
| `name` | texto | no | Nombre del documento, hasta 255 caracteres. Si se omite, se usa el nombre del archivo sin extensión |

```bash
curl.exe -k -F "name=informe" -F "file=@informe.pdf;type=application/pdf" https://orchestrator.proyecto.localhost/api/v1/documents
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
  "processed_at": "2026-10-23T13:50:47.603Z",
  "updated_at": "2026-10-23T13:50:47.603Z"
}
```

`document_id` es el id que asigna document-store; `checksum` es el SHA-256 de los bytes del
PDF, calculado por el orquestador. `processed_at` es cuándo se guardó y `updated_at`, la última
modificación (igual a `processed_at` hasta que se renombra).

### `GET /api/v1/documents`

| Query | Default | Reglas |
|---|---|---|
| `skip` | `0` | Documentos a saltear, `>= 0` |
| `limit` | `10` | Máximo a devolver, de `1` a `100` |

```bash
curl.exe -k "https://orchestrator.proyecto.localhost/api/v1/documents?skip=0&limit=10"
```

`200` con una lista de `ProcessedDocument` (vacía si no hay documentos en esa página). Cada
elemento incluye `extracted_text` completo, igual que en document-store. Un `skip` o `limit`
fuera de rango responde `422` sin llamar a document-store.

### `GET /api/v1/documents/{document_id}`

```bash
curl.exe -k https://orchestrator.proyecto.localhost/api/v1/documents/1
```

`200` con el documento, o `404` si no existe. Un id que no es un entero positivo (`0`, `-5`,
`abc`) responde `422`.

### `PUT /api/v1/documents/{document_id}`

Body JSON: solo `name`, de 1 a 255 caracteres sin contar los espacios de los extremos (se
quitan). Mismas reglas que document-store, pero validadas en el orquestador: un nombre vacío,
demasiado largo, que no es texto, o un campo extra responde `422` sin llamar a document-store.

```powershell
'{"name": "Informe final"}' | Out-File -Encoding ascii renombrar.json
curl.exe -k -X PUT -H "Content-Type: application/json" --data-binary "@renombrar.json" https://orchestrator.proyecto.localhost/api/v1/documents/1
```

`200` con el documento completo: cambian `name` y `updated_at`; el resto queda igual. `404` si no
existe.

### `DELETE /api/v1/documents/{document_id}`

```bash
curl.exe -k -X DELETE https://orchestrator.proyecto.localhost/api/v1/documents/1
```

`204` sin cuerpo, o `404` si no existe (por ejemplo, al borrarlo dos veces).

### Colección de Postman

[`docs/postman/orchestrator.postman_collection.json`](docs/postman/orchestrator.postman_collection.json)
tiene las cinco requests, en orden: subir, listar, ver, renombrar y borrar.

1. En Postman: **Import** → elegir el archivo.
2. En **1. Subir documento** → **Body** → `file`: elegir un PDF.
3. Enviar las requests en orden. El script de test de *Subir* guarda el `document_id` de la
   respuesta en la variable de colección `documentId`, que usan las otras tres.

| Variable | Valor | Para qué |
|---|---|---|
| `baseUrl` | `https://orchestrator.proyecto.localhost` | El orquestador detrás de Traefik |
| `documentId` | (se completa solo) | Id del último documento subido |

Cada request tiene la verificación SSL desactivada (`strictSSL: false`), porque Traefik usa un
certificado local de mkcert. Para probar sin Docker, cambiar `baseUrl` a `http://localhost:8000`.

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
| `404` | No existe un documento con ese id (ver, renombrar o borrar) | `DocumentNotFoundError` |
| `409` | El PDF ya estaba guardado: document-store tiene un documento con el mismo checksum | `DocumentAlreadyExistsError` |
| `413` | El PDF supera `MAX_PDF_SIZE_BYTES` (10 MB). Se corta sin terminar de leerlo | `PdfTooLargeError` |
| `422` | Falta el campo `file`, el id no es un entero positivo, `skip`/`limit` fuera de rango, el `name` del `PUT` es inválido o trae campos extra, u otro error de validación de la request. No se llama a ningún servicio | — |
| `502` | pdf-extractext o document-store respondió otro 5xx o un cuerpo que no se puede interpretar | `PdfExtractUnexpectedResponseError`, `DocumentStoreUnexpectedResponseError` |
| `502` | document-store rechazó los datos con otro 4xx: el orquestador ya los validó, así que es un contrato roto o una URL mal configurada | `DocumentStoreRejectedError` |
| `503` | No se pudo conectar con el servicio (tras agotar los reintentos), Traefik no pudo conectar con él (`502`), el circuito está abierto en Traefik o no quedan réplicas sanas (`503` de Traefik, sin `Retry-After`), o document-store no tiene MongoDB (`503` sin `Retry-After`) | `PdfExtractUnavailableError`, `DocumentStoreUnavailableError` |
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
| `HOST` | `0.0.0.0` | Interfaz en la que escucha uvicorn |
| `PORT` | `8000` | Puerto de uvicorn. En Docker también lo usa el `HEALTHCHECK`: se cambia sin reconstruir la imagen |
| `DEBUG` | `false` | `true` activa el reload de uvicorn. Solo para desarrollo |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | IPs o redes (CIDR) de las que se aceptan los headers `X-Forwarded-*`. Detrás de Traefik: la subred de la red del compose (p. ej. `172.28.0.0/16`) |
| `GRACEFUL_SHUTDOWN_SECONDS` | `60` | Segundos que uvicorn espera las requests en curso al recibir `SIGTERM`. Menor que el `stop_grace_period` del compose |
| `PDF_EXTRACT_BASE_URL` | `http://localhost:8000` | URL base de pdf-extractext. En el stack: `http://traefik:8081` (entrypoint interno de Traefik, con balanceo y circuit breaker) |
| `DOCUMENT_STORE_BASE_URL` | `http://localhost:8002` | URL base de document-store. En el stack: `http://traefik:8081` |
| `PDF_EXTRACT_TIMEOUT_SECONDS` | `30` | Timeout de lectura/escritura hacia pdf-extractext. Mayor que los 25 s en los que pdf-extractext responde 503 si no llega a extraer (`EXTRACT_MAX_WAIT_SECONDS`), para recibir ese 503 en vez de cortar una extracción que iba a terminar. Acepta también el nombre anterior, `HTTP_TIMEOUT_SECONDS` |
| `DOCUMENT_STORE_TIMEOUT_SECONDS` | `5` | Timeout de lectura/escritura hacia document-store, que solo guarda un JSON |
| `HTTP_CONNECT_TIMEOUT_SECONDS` | `2` | Timeout solo para conectar, el mismo para los dos: más corto, para fallar rápido si un servicio está caído |
| `RETRY_MAX_ATTEMPTS` | `3` | Intentos máximos por llamada, contando el primero |
| `RETRY_BACKOFF_SECONDS` | `0.2` | Base de la espera exponencial entre reintentos |
| `RETRY_MAX_BACKOFF_SECONDS` | `2.0` | Tope de esa espera |
| `BULKHEAD_MAX_CONCURRENT` | `5` | Requests en vuelo hacia cada microservicio por proceso (un bulkhead por servicio) |
| `BULKHEAD_MAX_WAITING` | `10` | Requests que pueden esperar un lugar |
| `BULKHEAD_ACQUIRE_TIMEOUT_SECONDS` | `5` | Máximo de espera por un lugar |
| `MAX_PDF_SIZE_BYTES` | `10485760` | Tamaño máximo del PDF (10 MB, igual que pdf-extractext). Más grande: `413` |

## Cómo ejecutar

Requisitos: [uv](https://docs.astral.sh/uv/) (instala Python 3.14 solo).

```bash
uv sync                          # instala dependencias (crea .venv)
cp .env.example .env             # PowerShell: copy .env.example .env
uv run python main.py            # levanta el servicio en HOST:PORT (default :8000); reload solo con DEBUG=true
```

Swagger en `http://localhost:8000/docs`.

Para probar el flujo completo hacen falta **pdf-extractext** y **document-store** corriendo.
Con los defaults, cada uno va en su puerto:

| Servicio | Puerto local | Cómo |
|---|---|---|
| pdf-extractext | `:8000` | su propio compose, desde `../pdf-extractext` (con Docker abierto) |
| orquestador | `:8001` | `PORT=8001` y `DEBUG=true` en `.env`, y `uv run python main.py` |
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
| Unitarios | `tests/unit/` | Validador, loader, service (con stubs de los dos clients), clients (HTTP mockeado con **respx**), Retry, Bulkhead, config, wiring | sin I/O real |
| Integración | `tests/integration/` | La app completa en memoria con `TestClient`: endpoints, handlers de error, lifespan; pdf-extractext y document-store simulados con respx | sin servicios externos |
| Sistema | `tests/system/` | Punta a punta contra el stack real (Traefik + orquestador + pdf-extractext + document-store + MongoDB) | opt-in, ver abajo |

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

Los tests de tolerancia a fallas (`test_fault_tolerance_system.py`) **detienen
document-store** con `docker compose stop` y lo vuelven a levantar al terminar, aunque el test
falle. Por eso exigen una segunda variable:

```bash
# bash
RUN_SYSTEM_TESTS=1 RUN_FAULT_TESTS=1 uv run pytest tests/system -o addopts=""
# PowerShell
$env:RUN_SYSTEM_TESTS=1; $env:RUN_FAULT_TESTS=1; uv run pytest tests/system -o addopts=""
```

| Variable | Default | Para qué |
|---|---|---|
| `RUN_SYSTEM_TESTS` | — | `1` habilita los tests de sistema; sin ella se saltean (la CI no tiene stack) |
| `RUN_FAULT_TESTS` | — | `1` habilita además los que detienen contenedores |
| `ORCHESTRATOR_BASE_URL` | `https://orchestrator.proyecto.localhost` | Despliegue contra el que se prueba |
| `INFRASTRUCTURE_DIR` | `../infrastructure` | Carpeta del `docker-compose.yml` del stack, donde se corre `docker compose` |
| `TRAEFIK_CIRCUIT_SECONDS` | `40` | `fallbackduration` + `recoveryduration` del circuit breaker de Traefik: cuánto reintentar después de restaurar un servicio |

```bash
# bash: el stack en otra carpeta
INFRASTRUCTURE_DIR=/ruta/a/infrastructure RUN_SYSTEM_TESTS=1 RUN_FAULT_TESTS=1 uv run pytest tests/system -o addopts=""
# PowerShell
$env:INFRASTRUCTURE_DIR="C:\ruta\a\infrastructure"; $env:RUN_SYSTEM_TESTS=1; $env:RUN_FAULT_TESTS=1; uv run pytest tests/system -o addopts=""
```

`-o addopts=""` evita que el umbral de cobertura de la suite regular aplique a estos tests.

En Windows, Python no resuelve `*.localhost` (`getaddrinfo failed`), aunque `curl.exe` y el
navegador sí. Para correr los tests de sistema desde Windows, agregar
`127.0.0.1 orchestrator.proyecto.localhost` al archivo `hosts` (como administrador).

### Escenarios del flujo y dónde se prueban (issue #22)

| Escenario | Unitario | Integración (respx) | Sistema (stack real) |
|---|---|---|---|
| Flujo exitoso: extrae, guarda y responde `201` | `test_process_pdf_extrae_el_texto_y_guarda_el_documento_en_document_store` | `test_post_documents_extrae_guarda_y_devuelve_el_documento` | `test_flujo_completo_extrae_guarda_y_devuelve_el_documento` |
| Checksum SHA-256 calculado por el orquestador | `test_pdf_document_calcula_el_checksum_sha256_de_su_contenido`, `test_process_pdf_envia_el_sha256_de_los_bytes_del_pdf` | mismo test del flujo: body enviado a document-store | mismo test del flujo: `checksum == sha256(bytes)` |
| Request enviada a document-store | `test_create_document_envia_el_json_del_contrato_y_mapea_el_201` | `test_post_documents_extrae_guarda_y_devuelve_el_documento` | — |
| Documento duplicado → `409` | `test_create_document_traduce_el_409_a_documento_duplicado` | `test_post_documents_responde_409_si_el_pdf_ya_estaba_guardado` | `test_el_mismo_pdf_dos_veces_responde_409_con_problem_details` |
| pdf-extractext rechaza el PDF → `400`, sin guardar | `test_si_falla_la_extraccion_no_se_guarda_nada`, `test_extract_traduce_un_4xx_a_rechazo_con_el_detalle` | `test_post_documents_propaga_el_rechazo_de_pdf_extractext_sin_guardar` | `test_un_pdf_danado_responde_400_y_no_se_guarda` |
| pdf-extractext falla (5xx, caído, timeout), sin guardar | `test_extract_traduce_una_respuesta_inesperada`, `test_extract_traduce_las_caidas` | `test_post_documents_informa_las_fallas_de_pdf_extractext_sin_guardar`, `test_post_documents_informa_fallos_de_red_hacia_pdf_extractext` | — |
| document-store no disponible → `503` | `test_create_document_traduce_los_fallos_de_red`, `test_create_document_trata_un_503_sin_retry_after_como_no_disponible` | `test_post_documents_informa_las_fallas_de_document_store[caido]`, `[sin-base]` | `test_document_store_caido_responde_503_y_al_volver_se_guarda_el_mismo_pdf` |
| document-store error interno → `502` | `test_create_document_traduce_5xx_a_respuesta_inesperada` | `test_post_documents_informa_las_fallas_de_document_store[5xx]` | — (no se puede provocar sin tocar código) |
| El orquestador no tiene persistencia propia | `test_sin_persistencia.py` | — | — |

### CRUD de documentos y dónde se prueba (issue #27)

| Escenario | Unitario | Integración (respx) | Sistema (stack real) |
|---|---|---|---|
| Listar, ver, renombrar y borrar | `test_document_store_client.py` (`test_list_documents_*`, `test_get_document_*`, `test_update_document_*`, `test_delete_document_*`), `test_document_service_crud.py` | `test_documents_crud.py` (camino feliz) | `test_subir_listar_ver_renombrar_borrar_y_despues_404` |
| Id inexistente → `404` | `test_*_traduce_el_404_a_documento_inexistente`, `test_un_id_inexistente_propaga_documento_no_encontrado` | `test_un_id_inexistente_responde_404_con_problem_details` | mismo test del flujo: `GET` y `DELETE` después de borrar |
| Pedido inválido → `422` sin llamar a document-store | `test_document_update.py` | `test_un_pedido_invalido_responde_422_sin_llamar_a_document_store` | `test_renombrar_con_un_nombre_invalido_responde_422_y_no_cambia_nada` |
| document-store caído o sin MongoDB → `503`, sin responder → `504` | `test_*_traduce_los_fallos_de_red`, `test_*_traduce_5xx_a_respuesta_inesperada` | `test_document_store_caido_responde_503`, `test_document_store_sin_responder_a_tiempo_responde_504` | — |
| GET, PUT y DELETE reintentan un 5xx; el alta no | `test_las_operaciones_idempotentes_reintentan_un_5xx`, `test_el_alta_no_reintenta_un_5xx` | — | — |
| Aparecen en `/docs` con sus errores | — | `test_los_endpoints_del_crud_aparecen_en_openapi_con_sus_errores` | — |

## Docker y Traefik

### Imagen del orquestador

Dockerfile multi-stage sobre `python:3.14-alpine`, con el binario de uv en versión fija
(`ghcr.io/astral-sh/uv:0.11.7`, la misma que document-store y pdf-extractext). Por qué Alpine y
no slim, con los números de grype: [ADR-0004](docs/decisions/0004-imagen-base-alpine-con-uv-fijo.md).

- Corre con el usuario sin privilegios `appuser`, no como root.
- Arranca con `CMD ["python", "main.py"]`: `main.py` lanza uvicorn con `HOST`, `PORT`,
  `FORWARDED_ALLOW_IPS` y `GRACEFUL_SHUTDOWN_SECONDS` de la configuración, y `python` queda como
  PID 1 para recibir el `SIGTERM` y apagarse en orden.
- `ENV PORT=8000` es el default; el `HEALTHCHECK` consulta `/health` con Python (la imagen no
  usa `wget`) en ese mismo `PORT`.

```bash
docker build -t orchestrator-service .
docker run --rm -p 8001:8000 -e PDF_EXTRACT_BASE_URL=http://host.docker.internal:8000 -e DOCUMENT_STORE_BASE_URL=http://host.docker.internal:8002 orchestrator-service
docker run --rm -p 9000:9000 -e PORT=9000 orchestrator-service    # otro puerto, misma imagen
```

### Stack completo con Traefik

El despliegue completo (Traefik como reverse proxy y balanceador, orquestador con réplicas,
pdf-extractext, document-store y MongoDB, todo en una red Docker) vive en la carpeta hermana
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

El orquestador solo confía en los headers `X-Forwarded-*` de las IPs o redes de
`FORWARDED_ALLOW_IPS` (default `127.0.0.1`). Detrás de Traefik, el compose tiene que pasarle la
subred de la red compartida (por ejemplo `FORWARDED_ALLOW_IPS=172.28.0.0/16`, con esa subred fija
en la red); si no, las URLs que arma el orquestador salen con `http://` en lugar de `https://`.

## Resiliencia

Retry y Bulkhead viven en `app/clients/` y se aplican en `BaseClient._request`, el único
punto por donde salen requests. Los services no saben que existen. Cada microservicio tiene
su propio Bulkhead. El **Circuit Breaker está en Traefik**, configurado con labels en el
`docker-compose.yml` de `../infrastructure`: un solo circuito por servicio para todas las
réplicas del orquestador ([ADR-0005](docs/decisions/0005-circuit-breaker-en-traefik.md)). El
detalle y las razones (basadas en mediciones de carga sobre pdf-extractext) están en
[ADR-0002](docs/decisions/0002-politica-de-resiliencia.md) y en el ADR-0005.

| Patrón | Módulo | Qué hace | Por qué así |
|---|---|---|---|
| **Retry** | `retry.py` | Reintenta solo fallos de conexión (con Traefik) y 5xx salvo `503` en métodos idempotentes; backoff exponencial con jitter. Una réplica caída la reintenta Traefik | Un POST que agota el timeout puede haberse procesado igual: reintentarlo duplica la extracción o termina en 409 en document-store |
| **Circuit Breaker** | Traefik (`cb-extract`, `cb-document-store`) | Con más de 30 % de errores de red o 25 % de 5xx abre el circuito: responde `503` en el acto durante 30 s y después deja pasar tráfico de a poco. Para pdf-extractext el `503` (cola llena) no cuenta | Seguir enviando PDFs a un hoja caído solo agranda su cola; uno sano pero ocupado ya se defiende solo |
| **Bulkhead** | `bulkhead.py` | Acota las requests en vuelo y en espera hacia cada microservicio; el exceso recibe 503 inmediato | Cada request retiene un PDF en memoria; sin tope, un hoja lento agota la memoria del orquestador |

Orden por cada intento: `Retry → Bulkhead → red (Traefik → circuit breaker → servicio)`. Con el
circuito abierto Traefik responde `503` sin `Retry-After`; el orquestador no lo reintenta y
responde `503` Problem Details.

## Arquitectura

Arquitectura de 3 capas con patrón MVC:

| Capa | Ubicación | Responsabilidad |
|---|---|---|
| Presentación | `app/routers/`, `app/schemas/`, `app/errors.py` | HTTP, validación de entrada (Pydantic), traducción de excepciones a Problem Details |
| Aplicación | `app/services/` | Validación del PDF, conversión a `PdfDocument`, orquestación |
| Infraestructura de salida | `app/clients/` | Clients HTTP (httpx) hacia cada microservicio; Retry y Bulkhead |

```
app/
├── main.py                 # create_app() + lifespan (crea/cierra los clients)
├── config.py               # Settings (pydantic-settings)
├── dependencies.py         # wiring: un client singleton por servicio, service, políticas de resiliencia
├── errors.py               # excepciones de dominio → Problem Details
├── exceptions.py           # excepciones de dominio
├── routers/   documents.py, health.py
├── schemas/   documents.py (PdfDocument, ProcessedDocument, DocumentUpdate), pdf_extract.py y document_store.py (contratos de los servicios)
├── services/  document_service.py, pdf_loader.py, pdf_validator.py
└── clients/   base_client.py, pdf_extract_client.py, document_store_client.py, retry.py, bulkhead.py
```

- Detalle de capas, componentes y guía para agregar funcionalidad:
  [`Project_Architecture_Blueprint.md`](Project_Architecture_Blueprint.md).
- Decisiones de diseño (ADRs): [`docs/decisions/`](docs/decisions/).
- Contratos que consume el orquestador, con errores en Problem Details:
  - pdf-extractext: `POST /extract` (multipart `file`) → `200 {"content", "page_count"}`.
  - document-store: `POST /api/v1/documents` (JSON con `name`, `original_filename`,
    `checksum`, `file_size`, `page_count`, `extracted_text`, `is_processed`) →
    `201` con el documento y su `id`; `409` si el checksum ya existe.
    `GET /api/v1/documents?skip=&limit=` → `200` con la lista; `GET`, `PUT` (`{"name"}`) y
    `DELETE /api/v1/documents/{id}` → `200`/`204`, o `404` si el id no existe.
