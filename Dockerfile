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
COPY main.py ./

# --- Etapa 2: imagen final, sin uv ni herramientas de build ---------------
FROM python:3.14-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    PORT=8000

WORKDIR /app

# Actualizar paquetes del sistema para mitigar vulnerabilidades (zlib, busybox).
# sqlite-libs no tiene versión corregida en el repositorio estable de Alpine
# (limitación conocida, #24).
RUN apk upgrade --no-cache

COPY --from=builder /app /app

# Solo documentación: el puerto real lo define PORT (main.py lo lee de Settings).
EXPOSE ${PORT}

# /health solo dice que el proceso está vivo. Se consulta con Python (no con
# wget) en el mismo PORT que usa la app; funciona igual en Alpine y en slim.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8000')}/health\", timeout=2)"]

# Forma exec: python queda como PID 1 y recibe el SIGTERM (apagado ordenado).
# main.py lanza uvicorn con HOST, PORT, proxies y timeout de Settings.
CMD ["python", "main.py"]
