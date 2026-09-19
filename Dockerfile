# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.14-alpine AS builder

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY app ./app

FROM python:3.14-alpine

WORKDIR /app

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
