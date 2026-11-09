# syntax=docker/dockerfile:1

# --- Etapa 1: construir el entorno virtual con uv --------------------------
# Base Alpine (y no slim como document-store y pdf-extractext): con grype da
# 17 vulnerabilidades contra 184 de slim. Ver docs/decisions/0004.
FROM python:3.14-alpine AS builder

# Binario de uv con versión fija (la misma que los otros dos servicios), solo
# en esta etapa: dos builds del mismo commit dan la misma imagen.
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /bin/uv

# Bytecode precompilado (arranque más rápido) y copias reales en vez de links,
# para poder mover el .venv a la imagen final. Se usa el Python de la imagen.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Primero solo las dependencias: esta capa se reutiliza mientras no cambie
# uv.lock, aunque cambie el código.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY app ./app

# --- Etapa 2: imagen final, sin uv ni herramientas de build ---------------
FROM python:3.14-alpine

WORKDIR /app

# Actualizar paquetes del sistema para mitigar vulnerabilidades (zlib, busybox)
# Nota: sqlite-libs 3.53.4-r0 permanece; Alpine 3.24 no tiene versión corregida en repositorio estable.
# Usar edge (3.54.1) mezclaría repositorios inestables; se documenta como limitación conocida.
RUN apk upgrade --no-cache

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app /app/app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8000/health || exit 1

# --proxy-headers: detras de Traefik (que termina TLS), tomar esquema y cliente
# reales de X-Forwarded-*; sin esto las URLs generadas dicen http://.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
