from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.config import Settings
from app.dependencies import get_redis, get_settings_dep
from app.limiter import limiter
from app.schemas.geocode import GeocodeSuggestion
from app.services.geocoding_service import google_place_details, google_places_autocomplete, photon_autocomplete

router = APIRouter(prefix="/api/v1/geocode", tags=["geocode"])


@router.get("/autocomplete")
@limiter.limit("30/minute")
async def autocomplete(
    request: Request,  # slowapi requires this exact parameter name
    settings: Annotated[Settings, Depends(get_settings_dep)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    q: Annotated[str, Query(min_length=2, max_length=200)],
    lang: str = "uk",
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
    session_token: str | None = None,
) -> list[GeocodeSuggestion]:
    if settings.MAPS_API_KEY and session_token:
        return await google_places_autocomplete(q, lang, limit, session_token, settings)
    return await photon_autocomplete(q, lang, limit, settings, redis=redis)


@router.get("/place-details")
@limiter.limit("30/minute")
async def place_details(
    request: Request,  # slowapi requires this exact parameter name
    settings: Annotated[Settings, Depends(get_settings_dep)],
    place_id: Annotated[str, Query(min_length=1, max_length=500)],
    session_token: Annotated[str, Query(min_length=1, max_length=200)],
    lang: str = "uk",
) -> GeocodeSuggestion:
    if not settings.MAPS_API_KEY:
        raise HTTPException(status_code=503, detail="Maps API not configured")
    return await google_place_details(place_id, session_token, settings, lang)
