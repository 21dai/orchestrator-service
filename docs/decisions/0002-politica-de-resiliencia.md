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

## Actualización (2026-10-20): dos fallas del Circuit Breaker

La devolución del profesor marcó que el Circuit Breaker y el Bulkhead estaban
mal. Se reprodujeron dos fallas y se corrigieron con TDD:

1. **El circuito quedaba trabado en half-open para siempre.** La llamada de
   prueba se marcaba en curso antes de pedir lugar en el Bulkhead; si el
   Bulkhead la rechazaba, o si la request se cancelaba (el usuario cortó la
   conexión: `CancelledError` no es `Exception`), nunca se registraba éxito ni
   fallo y el breaker rechazaba todo hasta reiniciar el proceso. Ahora
   `before_request()` avisa si la request es la prueba y `BaseClient` la libera
   en un `finally` (`release_trial()`) cuando termina sin resultado.
2. **La cola llena de pdf-extractext abría el circuito.** Desde su versión
   1.3.0, pdf-extractext responde `503` con `Retry-After` cuando no da abasto
   (backpressure): está sano, solo ocupado. El breaker lo contaba como fallo,
   así que en un pico cinco rechazos cortaban 30 s un servicio sano, y el
   cliente recibía `502`. Ahora un `503` con `Retry-After` no cuenta ni como
   éxito ni como fallo, y el orquestador responde `503` con el mismo
   `Retry-After`. Un `503` sin ese header (el de Traefik cuando no queda
   ninguna réplica sana) sigue contando como fallo.

El circuito abierto también devuelve `Retry-After` con lo que falta para la
llamada de prueba.

## Actualización (2026-10-23): document-store y un timeout por servicio

Con la integración de document-store (ADR-0003, issue #21):

- **Un Circuit Breaker y un Bulkhead por microservicio.** document-store tiene los suyos,
  separados de los de pdf-extractext: si uno se cae, no se corta el tráfico hacia el otro.
  Usan los mismos valores de configuración (`CIRCUIT_BREAKER_*`, `BULKHEAD_*`).
- **Un timeout por microservicio.** El de pdf-extractext pasó de 10 s a **30 s**
  (`PDF_EXTRACT_TIMEOUT_SECONDS`): pdf-extractext responde `503` con `Retry-After` antes de
  25 s si no llega a extraer a tiempo (`EXTRACT_MAX_WAIT_SECONDS`). Con 10 s, el orquestador
  cortaba con `504` una extracción que pdf-extractext iba a terminar o a rechazar
  ordenadamente, y el trabajo se perdía. El de document-store es **5 s**
  (`DOCUMENT_STORE_TIMEOUT_SECONDS`): solo guarda un JSON. `HTTP_TIMEOUT_SECONDS`, el nombre
  anterior, sigue configurando el de pdf-extractext.
- **Retry igual para los dos.** Los `POST` no se reintentan ante timeouts ni 5xx: un alta
  en document-store que agotó el timeout puede haberse guardado igual, y reintentarla
  terminaría en `409`.
- **document-store sin MongoDB responde `503` sin `Retry-After`**: el breaker lo cuenta
  como fallo (es una caída, no una carga pasajera) y el orquestador responde `503`.
