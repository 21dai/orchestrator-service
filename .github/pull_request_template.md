## Qué cambia

<!-- Resumen corto. Referenciar la issue: Closes #N -->

## Checklist de calidad

- [ ] `uv run pytest` en verde (cobertura >= 90 %)
- [ ] `uv run ruff check .` y `uv run ruff format --check .` limpios
- [ ] `docker build -t orchestrator-service .` construye y `/health` responde
- [ ] Sin secretos: nada de `.env`, credenciales ni certificados en el diff
- [ ] Commits con formato `tipo(#N): descripcion` y `Closes #N` cuando cierra la issue
- [ ] README / Blueprint / ADRs actualizados si cambió la API, la configuración o una decisión de diseño
