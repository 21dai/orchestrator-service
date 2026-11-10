# ADR-0005: Circuit Breaker en Traefik

## Estado

Aceptado. Reemplaza la sección *Circuit Breaker* del
[ADR-0002](0002-politica-de-resiliencia.md) y ajusta su política de Retry.

## Fecha

2026-11-10

## Contexto

- La cátedra (clase del 04/11) pidió que el patrón cortocircuito lo maneje **Traefik** y que
  esté **configurado en el Docker Compose**. Hasta ahora estaba programado a mano en el
  orquestador (`app/clients/circuit_breaker.py`, ADR-0002).
- Cada réplica del orquestador tenía su propio breaker en memoria: con 3 réplicas había 3
  circuitos que no se enteraban entre sí. Traefik ve todo el tráfico y aplica una sola regla.
- El orquestador llamaba directo a `http://pdf-extractext:8000` y a
  `http://document-store:8000`. Traefik no veía ese tráfico, y el balanceo entre las 5
  réplicas de pdf-extractext dependía del DNS de Docker y del pool de conexiones de httpx.
- Dos breakers encadenados (Traefik + código) hacen difícil saber cuál se abrió y cuándo.

21dai/infrastructure#6 agregó un entrypoint **interno** de Traefik (`:8081`, sin publicar)
con un router por servicio y un middleware `circuitbreaker` en cada uno.

## Decisión

1. El orquestador llama a los dos servicios a través de Traefik:
   `PDF_EXTRACT_BASE_URL` y `DOCUMENT_STORE_BASE_URL` valen `http://traefik:8081` en el stack.
   Traefik elige el servicio por el path (`/extract` o `/api/v1/documents`).
2. **Se saca el Circuit Breaker del código**: `circuit_breaker.py`, las variables
   `CIRCUIT_BREAKER_*` y las excepciones `*CircuitOpenError`. El circuito lo abre Traefik:
   - `cb-extract`: más de 30 % de errores de red o más de 25 % de 5xx, **sin contar el 503**
     (backpressure de pdf-extractext, misma regla que antes aplicaba `is_load_shedding`).
   - `cb-document-store`: más de 30 % de errores de red o más de 25 % de 5xx (el 503 sí
     cuenta: MongoDB caído).
   - Abierto 30 s (`fallbackduration`), recuperación gradual 10 s (`recoveryduration`).
3. **El Bulkhead se queda** en el código: limita los PDFs en memoria de cada proceso del
   orquestador, y eso Traefik no lo puede hacer.
4. Traducción de las respuestas de Traefik:

   | Traefik responde | Cuándo | El orquestador responde |
   |---|---|---|
   | `503` sin `Retry-After` | Circuito abierto o sin réplicas sanas | `503` Problem Details, sin `Retry-After` (`*UnavailableError`) |
   | `502` | No pudo conectar con el servicio (caído) | `503` Problem Details (`*UnavailableError`) |

   Ninguno de los dos servicios responde `502` por sí mismo, así que un `502` siempre viene
   de Traefik. Antes se traducía a `502` "respuesta inesperada", que no describía la caída.
5. **Retry: un solo lugar por tipo de reintento**, para no multiplicar intentos (3 del código
   × 3 de Traefik = 9):

   | Falla | Quién reintenta |
   |---|---|
   | No se puede conectar con Traefik (`ConnectError`, `ConnectTimeout`) | El orquestador (`retry.py`) |
   | Una réplica de pdf-extractext caída o apagándose | Traefik (middleware `extract-reintento`, 3 intentos) |
   | 5xx en `GET`/`PUT`/`DELETE` hacia document-store (salvo `503`) | El orquestador; Traefik no tiene `retry` en ese router |
   | `503` (circuito abierto, MongoDB caído, cola llena) | Nadie: reintentar enseguida no cambia nada y le suma fallos al breaker |
   | Timeouts y 5xx de un `POST` | Nadie (sin cambios respecto del ADR-0002) |

## Alternativas consideradas

### Dejar los dos breakers (Traefik y código)

- Pros: el orquestador seguiría protegido si se lo despliega sin Traefik.
- Contras: dos circuitos encadenados con reglas distintas; cuál se abre primero depende de
  la carga y es difícil de explicar y de probar. Contradice el pedido de la cátedra.
- Rechazada.

### Routers internos solo en las labels de cada servicio

- Pros: cada servicio declara su propio router.
- Contras: Traefik arma los routers de Docker con los contenedores que están corriendo. Con
  document-store parado, su router desaparecía y Traefik respondía `404`, que el orquestador
  interpretaba como "documento no encontrado", sin pasar nunca por el breaker.
- Rechazada para document-store y como respaldo de `/extract`: esos routers van en las labels
  de `traefik` con la URL del servicio (`loadbalancer.server.url`), así existen siempre. El
  router principal de `/extract` sigue en las labels de pdf-extractext, para que Traefik
  balancee entre las réplicas.

## Consecuencias

- El orquestador tiene menos código (KISS, YAGNI): `BaseClient` solo aplica Retry y Bulkhead.
- Un solo circuito por servicio para todas las réplicas del orquestador.
- Corriendo el orquestador **fuera** del stack (local, con las URLs directas a cada servicio)
  no hay circuit breaker: un servicio caído responde `503` después de los reintentos de
  conexión. Es aceptable: el despliegue soportado es el del compose.
- Con el circuito abierto el cliente recibe `503` **sin** `Retry-After`: Traefik no informa
  cuánto falta para la recuperación.
- Los tests de sistema (`tests/system/test_fault_tolerance_system.py`) verifican que, con
  document-store caído, el circuito de Traefik se abre (503 en menos de 1 s) y se cierra
  solo después de `fallbackduration` + `recoveryduration`.
