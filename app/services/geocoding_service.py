from __future__ import annotations

import json
from typing import TYPE_CHECKING

import aiohttp
import structlog

if TYPE_CHECKING:
    import redis.asyncio as aioredis

from app.config import Settings
from app.schemas.geocode import GeocodeSuggestion

logger = structlog.get_logger(__name__)


async def photon_autocomplete(
    q: str,
    lang: str,
    limit: int,
    settings: Settings,
    redis: aioredis.Redis | None = None,
) -> list[GeocodeSuggestion]:
    """Geocode via Photon/OSM with optional Redis cache.

    Pass a ``redis`` client (from ``get_redis`` dependency) to enable caching.
    The caller is responsible for managing the Redis connection lifecycle.
    """
    from fastapi import HTTPException, status

    cache_key = f"geocode:{q.strip().lower()}:{lang}:{limit}"

    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached:
                data = json.loads(cached)
                return [GeocodeSuggestion(**item) for item in data]
        except Exception as exc:
            logger.warning("Redis cache read failed", error=str(exc))

    photon_lang = lang if lang in {"default", "de", "en", "fr"} else "en"

    try:
        timeout = aiohttp.ClientTimeout(total=settings.PHOTON_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{settings.PHOTON_URL}/api/",
                params={"q": q, "limit": limit, "lang": photon_lang},
                headers={"User-Agent": "BookClub/1.0"},
            ) as response:
                response.raise_for_status()
                data = await response.json()
    except Exception as exc:
        logger.error("Photon geocoding request failed", error=str(exc), exc_type=type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Geocoding service unavailable") from exc

    suggestions: list[GeocodeSuggestion] = []
    for feature in data.get("features", []):
        props = feature["properties"]
        raw_parts = (props.get("name", ""), props.get("city", ""), props.get("country", ""))
        label_parts = [part for part in raw_parts if part]
        suggestions.append(
            GeocodeSuggestion(
                label=", ".join(label_parts),
                city=props.get("city") or props.get("county"),
                country=props.get("country"),
                lat=feature["geometry"]["coordinates"][1],
                lng=feature["geometry"]["coordinates"][0],
            )
        )

    if redis is not None:
        try:
            await redis.set(cache_key, json.dumps([s.model_dump() for s in suggestions]), ex=86400)
        except Exception as exc:
            logger.warning("Redis cache write failed", error=str(exc))

    return suggestions


async def google_places_autocomplete(
    q: str, lang: str, limit: int, session_token: str, settings: Settings
) -> list[GeocodeSuggestion]:
    from fastapi import HTTPException, status

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://places.googleapis.com/v1/places:autocomplete",
                headers={
                    "X-Goog-Api-Key": settings.MAPS_API_KEY,
                    "X-Goog-SessionToken": session_token,
                    "Content-Type": "application/json",
                },
                json={"input": q, "languageCode": lang},
            ) as response:
                response.raise_for_status()
                data = await response.json()
    except Exception as exc:
        logger.error("Google Places autocomplete failed", error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Geocoding service unavailable") from exc

    suggestions: list[GeocodeSuggestion] = []
    for item in data.get("suggestions", [])[:limit]:
        pred = item.get("placePrediction", {})
        structured = pred.get("structuredFormat", {})
        main = structured.get("mainText", {}).get("text", "")
        secondary = structured.get("secondaryText", {}).get("text", "")
        label = f"{main}, {secondary}" if secondary else main
        suggestions.append(
            GeocodeSuggestion(
                label=label or pred.get("text", {}).get("text", ""),
                place_id=pred.get("placeId"),
            )
        )

    return suggestions


async def google_place_details(
    place_id: str, session_token: str, settings: Settings
) -> GeocodeSuggestion:
    from fastapi import HTTPException, status

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://places.googleapis.com/v1/places/{place_id}",
                headers={
                    "X-Goog-Api-Key": settings.MAPS_API_KEY,
                    "X-Goog-SessionToken": session_token,
                    "X-Goog-FieldMask": "id,location,formattedAddress,addressComponents,shortFormattedAddress",
                },
            ) as response:
                response.raise_for_status()
                data = await response.json()
    except Exception as exc:
        logger.error("Google Place details failed", error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Geocoding service unavailable") from exc

    city: str | None = None
    country: str | None = None
    for component in data.get("addressComponents", []):
        types = component.get("types", [])
        if city is None and ("locality" in types or "administrative_area_level_2" in types):
            city = component.get("longText")
        if country is None and "country" in types:
            country = component.get("longText")

    location = data.get("location", {})
    return GeocodeSuggestion(
        label=data.get("formattedAddress", ""),
        city=city,
        country=country,
        lat=location.get("latitude"),
        lng=location.get("longitude"),
        place_id=data.get("id", place_id),
    )
