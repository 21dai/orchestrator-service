# ADR-0001: Representación interna del PDF

## Estado

Aceptado

## Fecha

2026-09-17

## Contexto

El orquestador recibe un PDF por `POST /api/v1/documents` (`multipart/form-data`,
campo `file`) y tiene que enviarlo al microservicio `pdf-extractext`, que lo
espera también como `multipart/form-data` (`name` + `file`) en su propio
`POST /api/v1/documents`.

La consigna habla de "convertir el binario del PDF". Interpretación adoptada:
el orquestador toma el binario que llega por HTTP y lo lleva a una
representación interna con la que trabajan sus capas (service → client), que
después se serializa nuevamente para el microservicio hoja. No implica cambiar
el formato del archivo (por ejemplo a Base64 o a texto): el PDF viaja intacto.

Restricciones que pesan en la decisión:

- El orquestador **no tiene estado ni persistencia** (Blueprint, §5): el
  archivo solo vive en memoria durante la request.
- `pdf-extractext` limita el tamaño a `MAX_PDF_SIZE_BYTES` = 10 MB. Un PDF de
  279 páginas (1,1 MB) tarda ~1,6 s en procesarse (mediciones del 2026-09-16).
- La validación del PDF (issue #2) necesita leer el contenido completo para
  verificar la firma `%PDF-`.
- Se busca que el `DocumentService` sea testeable sin depender de tipos de
  FastAPI (`UploadFile`).

## Decisión

**Trabajar con `bytes` en memoria.**

- El router lee el `UploadFile` completo (`await file.read()`) y entrega al
  service el contenido como `bytes` junto con `filename` y `content_type`.
- El service valida y arma un objeto de dominio inmutable, `PdfDocument`
  (`filename`, `content_type`, `content: bytes`), que es la representación
  interna que circula entre service y client (se implementa en la issue #4).
- El client de `pdf-extractext` serializa ese objeto de vuelta a
  `multipart/form-data`, sin transformar el contenido (issue #6).

## Alternativas consideradas

### Base64

- Pros: representable dentro de un JSON; cómodo si el microservicio hoja
  esperara JSON.
- Contras: `pdf-extractext` espera multipart, así que habría que codificar y
  decodificar en cada salto; +33 % de tamaño en memoria y en la red; costo de
  CPU innecesario.
- Rechazada: agrega trabajo y bytes sin ningún beneficio para este flujo.

### Stream (reenviar el archivo sin cargarlo entero)

- Pros: menor uso de memoria por request; permitiría PDFs muy grandes.
- Contras: la validación de firma y el cálculo de tamaño obligan a leer igual
  parte del contenido; el Retry (issue #16) no puede re-enviar un stream ya
  consumido; complica los tests con mocks. Con el límite de 10 MB del servicio
  hoja, la ganancia de memoria es marginal.
- Rechazada por ahora: la complejidad no se justifica con el tamaño máximo
  actual. Si el límite creciera mucho, se revisaría esta decisión.

### Archivo temporal en disco

- Pros: memoria acotada.
- Contras: introduce I/O de disco y estado en un servicio que por diseño no
  tiene ninguno; complica el despliegue con réplicas.
- Rechazada: contradice el Blueprint (sin estado).

## Consecuencias

- Cada request mantiene el PDF completo en memoria (máximo ~10 MB). Con
  réplicas y el Bulkhead (issue #18) limitando uploads concurrentes, el uso de
  memoria queda acotado y predecible.
- `PdfDocument` es un objeto plano e inmutable: el service y el client se
  prueban con `bytes` de fixture, sin FastAPI ni archivos reales.
- El Retry (issue #16) puede reintentar una llamada porque los `bytes` siguen
  disponibles (aunque, según las mediciones, un timeout de `POST` no debe
  reintentarse).
- El orquestador no interpreta el contenido del PDF (no usa `pypdf`): eso es
  responsabilidad del microservicio hoja.

## Actualización (2026-10-20): el PDF no pasaba por memoria

La devolución del profesor pidió revisar la validación del orquestador, y la
revisión encontró que esta decisión no se cumplía del todo:

- Con `UploadFile` de FastAPI, Starlette guarda en un **archivo temporal en
  disco** toda parte del multipart de más de 1 MB: justo la alternativa que
  este ADR rechaza.
- No había límite de tamaño: un archivo enorme se leía entero antes de
  validarlo, y el peor caso de memoria del Bulkhead (PDFs de hasta 10 MB) no
  estaba garantizado.

Ahora el router lee el multipart con `app/uploads.py` (la misma técnica que
pdf-extractext): el umbral para pasar a disco es el tamaño máximo aceptado, y
el body se corta con `413` por `Content-Length` o apenas supera
`MAX_PDF_SIZE_BYTES` (10 MB). Todo lo aceptado queda en memoria.
