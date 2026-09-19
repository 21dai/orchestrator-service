# ADR-0002: Política de resiliencia hacia pdf-extractext

## Estado

Aceptado

## Fecha

2026-09-19

## Contexto

El orquestador depende de `pdf-extractext` para procesar cada PDF. La consigna pide
aplicar Retry, Circuit Breaker y Bulkhead. Cómo configurarlos no es arbitrario: sale
de las mediciones de carga hechas sobre pdf-extractext el 2026-09-16 (Vegeta y k6,
API en Docker, un proceso):

- Un `POST` con un PDF de 279 páginas (1,1 MB) tarda **1,6 s** y devuelve 900 KB.
- `GET /health` a 50 req/s: p50 5 ms. Pero mientras se procesa un `POST` grande,
  `/health` sube a 491 ms de mediana: **la extracción bloquea el event loop** del hoja.
- `POST` grandes a 2/s: **27 % de éxito, mediana 30 s (timeout)**. Capacidad real:
  ~0,6 uploads grandes por segundo.
- El hoja **no descarta trabajo**: procesa todo aunque el cliente se haya ido. Un
  `POST` que agotó el timeout del cliente igual termina creando el documento.

Restricciones propias:

- Cada request al orquestador retiene el PDF completo en memoria (ADR-0001).
- El orquestador corre con réplicas detrás de Traefik: cada proceso tiene su propio
  estado de resiliencia (no hay estado compartido, por diseño).
- Stack deliberadamente mínimo: sin librerías nuevas si la lógica es chica.

## Decisión

Los tres patrones se implementan a mano en `app/clients/` y se aplican en
`BaseClient._request`, el único punto de salida. Orden por cada intento:
**Retry → Circuit Breaker → Bulkhead → red**.

### Retry (`retry.py`)

| Situación | ¿Reintenta? | Motivo |
|---|---|---|
| `ConnectError`, `ConnectTimeout` | Sí | La conexión nunca se estableció: la request no llegó |
| `ReadTimeout`, `WriteTimeout`, conexión cortada | **No** | La request ya salió; el hoja probablemente la está procesando. Reintentar duplica trabajo y termina en 400 por checksum duplicado |
| 5xx en GET/HEAD/PUT/DELETE/OPTIONS | Sí | Idempotentes |
| 5xx en POST | **No** | Podría duplicar trabajo |
| 4xx | No | Error del pedido |

Máximo 3 intentos (el original + 2). Espera con *full jitter*: uniforme en
`[0, min(0,2 s · 2ⁿ⁻¹, 2 s)]`, para que las réplicas no reintenten sincronizadas.

### Circuit Breaker (`circuit_breaker.py`)

Estados cerrado / abierto / half-open. Se abre con **5 fallos consecutivos** (errores
de transporte, timeouts o 5xx; un 4xx cuenta como éxito porque el hoja respondió) y
permanece abierto **30 s**; después deja pasar **una** llamada de prueba. Un breaker por
microservicio, compartido por todas las requests del proceso.

Los timeouts consecutivos son la señal principal de saturación del hoja; abrir el
circuito deja de alimentarle la cola y le da tiempo a recuperarse.

### Bulkhead (`bulkhead.py`)

Por proceso: **5 requests en vuelo** hacia pdf-extractext, **10 en espera**, espera
máxima **5 s**. El exceso recibe 503 inmediato. Un rechazo del Bulkhead no cuenta como
fallo para el breaker (el hoja no falló).

Con 5 en vuelo el hoja ya está por encima de su capacidad medida (~0,6 uploads/s ·
1,6 s ≈ 1 en curso); la cola de 10 absorbe ráfagas cortas. Peor caso de memoria por
proceso: 15 PDFs de hasta 10 MB.

### Ciclo de vida

El client (y con él Retry, Circuit Breaker y Bulkhead) se crea **al arrancar la app**
(lifespan), no perezosamente en la primera request: `get_pdf_extract_client` es una
dependencia síncrona que FastAPI ejecuta en un threadpool, y `lru_cache` no es atómico
entre hilos; con creación perezosa, varias requests simultáneas construían cada una su
propio client y los patrones no compartían estado (bug encontrado en la prueba de carga
del Bulkhead).

## Alternativas consideradas

### Librería de resiliencia (tenacity, aiobreaker, purgatory)

- Pros: probadas, menos código propio.
- Contras: ninguna cubre los tres patrones; las reglas de qué reintentar son
  específicas de este hoja (no reintentar timeouts de POST) y hay que escribirlas igual;
  suman dependencias a un stack que se quiere mínimo.
- Rechazada: la lógica son ~250 líneas testeadas; la regla que importa es de dominio.

### Reintentar también los timeouts

- Pros: más "robusto" en apariencia.
- Contras: las mediciones muestran que el hoja sigue procesando la request original;
  el reintento se encola detrás, empeora la saturación y falla con 400 por duplicado.
- Rechazada.

### Estado de resiliencia compartido entre réplicas (Redis)

- Pros: todas las réplicas abrirían el circuito a la vez.
- Contras: agrega un backing service y estado a un servicio sin estado; con 2-3
  réplicas cada una detecta la caída en segundos de todos modos.
- Rechazada por ahora.

## Consecuencias

- Los services no saben que existe resiliencia: se prueban con un stub del client.
- Cada patrón es un objeto chico con reloj / sleep inyectables: los tests no duermen.
- Los valores son configurables por entorno (`RETRY_*`, `CIRCUIT_BREAKER_*`,
  `BULKHEAD_*`) sin tocar código; los defaults documentados acá son el punto de
  partida y deberían revisarse si cambian las mediciones del hoja.
- Un cliente del orquestador puede recibir 503 aun con pdf-extractext sano
  (Bulkhead lleno): es la señal de que hay que reintentar más tarde o escalar
  réplicas, no un error.
