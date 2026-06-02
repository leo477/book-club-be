from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import Settings
from app.dependencies import get_settings_dep

router = APIRouter(prefix="/api/v1/config", tags=["config"])


@router.get("/maps-key")
async def get_maps_key(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, str]:
    return {"mapsApiKey": settings.MAPS_API_KEY, "mapsMapId": settings.MAPS_MAP_ID}
