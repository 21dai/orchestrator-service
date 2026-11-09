# ADR-0004: Imagen base Alpine con uv de versión fija

## Estado

Aceptado

## Fecha

2026-11-09

## Contexto

El builder del `Dockerfile` era `ghcr.io/astral-sh/uv:python3.14-alpine`, sin versión de uv:
dos builds del mismo commit podían dar imágenes distintas (12-Factor V y X). Además, el
orquestador era el único servicio en Alpine; document-store y pdf-extractext usan
`python:3.14-slim` con `COPY --from=ghcr.io/astral-sh/uv:0.11.7`.

El issue #31 pidió fijar uv y evaluar el paso a slim comparando las dos imágenes con grype, con
una regla: si slim da peor resultado que Alpine, quedarse en Alpine con uv fijo.

## Medición

grype sobre las dos imágenes construidas el 2026-11-09 (mismo código, misma base de datos de
vulnerabilidades):

| Imagen | Total | High | Medium | Low / Negligible | Con fix disponible | Tamaño |
|---|---|---|---|---|---|---|
| `python:3.14-alpine` + `apk upgrade` | 17 | 4 | 11 | 2 | 3 | 130 MB |
| `python:3.14-slim` | 184 | 61 | 62 | 61 | 6 | 238 MB |

- Alpine: las 4 High son de `sqlite-libs` 3.53.4-r0, sin versión corregida en el repositorio
  estable (limitación conocida, #24). Las 3 con fix son del intérprete Python (se corrigen en
  3.15/3.16).
- slim: 178 de las 184 están marcadas por Debian como *wont-fix* o *not-fixed* (util-linux,
  glibc, gcc, perl, ncurses, sqlite). Las 6 con fix son 3 de zlib (se irían con `apt-get
  upgrade`) y las mismas 3 de Python.

## Decisión

Quedarse en **`python:3.14-alpine`** en las dos etapas y copiar el binario de uv con versión
fija, la misma que los otros dos servicios: `COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /bin/uv`.
La estructura del `Dockerfile` (variables `UV_*`, capas, `COPY main.py`, usuario sin
privilegios, healthcheck con Python, `CMD ["python", "main.py"]`) es la misma que la de
document-store; solo cambia la base y la sintaxis de `addgroup`/`adduser`.

## Consecuencias

- Build reproducible: ninguna imagen del `Dockerfile` usa un tag sin versión.
- 17 vulnerabilidades en lugar de 184 y la mitad de tamaño.
- Las recetas de los tres servicios difieren solo en la base. Si una nueva medición da a slim
  igual o mejor que Alpine, se puede migrar sin tocar nada más que esas líneas.
- Alpine usa musl: `uvloop` y `httptools` (de `uvicorn[standard]`) se instalan desde wheels
  musllinux. Hasta ahora funcionan sin problemas.
- La versión de uv se sube en los tres servicios juntos (la última es 0.13.7, en una tarea
  aparte).
