from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.config import Settings
from app.dependencies import get_settings_dep
from app.limiter import limiter

router = APIRouter(prefix="/api/v1/config", tags=["config"])


@router.get("/maps-key")
@limiter.limit("5/minute")
async def get_maps_key(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, str]:
    return {"mapsApiKey": settings.MAPS_API_KEY, "mapsMapId": settings.MAPS_MAP_ID}
