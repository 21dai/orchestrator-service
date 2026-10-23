# ADR-0003: Coordinar la extracción y la persistencia

## Estado

Aceptado

## Fecha

2026-10-23

## Contexto

Hasta ahora el orquestador mandaba el PDF al CRUD de pdf-extractext
(`POST /api/v1/documents`), que extraía el texto **y** lo guardaba en su MongoDB. La
devolución del profesor marcó que el client apuntaba a ese path en lugar de al endpoint de
extracción, que faltaba la URL del otro microservicio como variable de entorno y que había
que revisar `process_pdf`.

El equipo separó las responsabilidades en dos microservicios:

- **pdf-extractext** expone `POST /extract`: recibe el PDF y devuelve
  `{"content", "page_count"}` sin guardar nada (sin estado).
- **document-store** expone `POST /api/v1/documents`: recibe el documento ya procesado en
  JSON, garantiza que el checksum SHA-256 sea único (`409` si se repite) y lo guarda en
  MongoDB. No recibe el PDF ni calcula el checksum (contrato de su issue #1).

Issue #21 de este repo.

## Decisión

El orquestador coordina los dos servicios en `DocumentService.process_pdf`:

1. Resuelve el nombre (si no viene, el del archivo sin extensión) y lo valida: más de 255
   caracteres → `400` **antes** de extraer, para no gastar una extracción en un documento
   que document-store va a rechazar. El nombre del archivo tiene el mismo límite.
2. Pide la extracción a `POST /extract`. Si falla, no se guarda nada.
3. Arma el documento: **el checksum lo calcula el orquestador** (SHA-256 de los bytes del
   PDF, `PdfDocument.checksum`), junto con el tamaño, el nombre, el texto y las páginas.
4. Lo guarda en document-store y devuelve al cliente el documento guardado, con el `id` de
   document-store y `page_count`.

Errores:

- **PDF repetido → `409 Conflict`** con el `detail` de document-store. El cliente recibe un
  error claro y no se crea un duplicado.
- Otro 4xx de document-store → `502`: el orquestador ya validó los datos, así que es un
  contrato roto o una URL mal configurada, no un error del cliente.
- Fallas de red, timeouts, circuito abierto o bulkhead lleno de cualquiera de los dos →
  `503`/`504` con el nombre del servicio en el `detail` (ver ADR-0002).

Cada servicio tiene su URL (`PDF_EXTRACT_BASE_URL`, `DOCUMENT_STORE_BASE_URL`), su timeout y
su propio Circuit Breaker y Bulkhead. La traducción de fallos común a los dos clients vive en
`BaseClient._call`; cada client solo resuelve lo propio de su contrato.

## Alternativas consideradas

### Saga con compensación

- Pros: el patrón habitual cuando una operación toca dos servicios.
- Contras: no hay nada que compensar. `/extract` no guarda nada; si falla el guardado, no
  queda ningún estado a medias en pdf-extractext.
- Rechazada: sería complejidad sin un problema que resolver.

### Devolver el documento existente en lugar de `409`

- Pros: subir dos veces el mismo PDF "funciona".
- Contras: document-store no tiene búsqueda por checksum; habría que agregarla a su
  contrato. Además oculta al cliente que el documento ya existía.
- Rechazada por ahora: se puede agregar después sin cambiar el resto del flujo.

### Buscar el checksum antes de extraer

- Pros: un PDF repetido no gastaría una extracción.
- Contras: necesita el mismo endpoint por checksum en document-store, y una llamada más en
  cada request para cubrir un caso poco frecuente.
- Rechazada por ahora, por la misma razón que la anterior.

## Consecuencias

- El orquestador sigue sin estado ni base de datos: no extrae texto ni persiste.
- Un PDF repetido gasta una extracción antes de recibir el `409`.
- El CRUD de pdf-extractext ya no se usa desde el orquestador; retirarlo es otro issue del
  equipo, igual que agregar document-store al stack de `infrastructure`. Hasta entonces, el
  stack de Docker no tiene a quién mandarle el guardado y el flujo responde `503`.
