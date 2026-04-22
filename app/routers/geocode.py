from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.config import Settings
from app.dependencies import get_settings_dep
from app.schemas.geocode import GeocodeSuggestion
from app.services.geocoding_service import photon_autocomplete

router = APIRouter(prefix="/api/v1/geocode", tags=["geocode"])


@router.get("/autocomplete")
async def autocomplete(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    q: Annotated[str, Query(min_length=2, max_length=200)],
    lang: str = "uk",
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> list[GeocodeSuggestion]:
    return await photon_autocomplete(q, lang, limit, settings)
