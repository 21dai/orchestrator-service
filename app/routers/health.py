from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Endpoint de salud para el orquestador y el balanceador (Traefik)."""
    return {"status": "ok"}
