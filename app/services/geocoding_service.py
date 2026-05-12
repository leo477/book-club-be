import asyncio
import json

import aiohttp
import structlog

from app.config import Settings
from app.schemas.geocode import GeocodeSuggestion

logger = structlog.get_logger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=2)
_MAX_RETRIES = 3
_RETRIABLE = (aiohttp.ClientError, asyncio.TimeoutError)


async def photon_autocomplete(q: str, lang: str, limit: int, settings: Settings) -> list[GeocodeSuggestion]:
    cache_key = f"geocode:{q.strip().lower()}:{lang}:{limit}"

    try:
        import redis.asyncio as aioredis

        redis = aioredis.from_url(settings.REDIS_URL)
        try:
            cached = await redis.get(cache_key)
            if cached:
                data = json.loads(cached)
                return [GeocodeSuggestion(**item) for item in data]
        finally:
            await redis.aclose()
    except Exception as exc:
        logger.warning("Redis cache read failed", error=str(exc))

    data = None
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        for attempt in range(_MAX_RETRIES):
            try:
                async with session.get(
                    f"{settings.PHOTON_URL}/api/",
                    params={"q": q, "limit": limit, "lang": lang},
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                break
            except _RETRIABLE as exc:
                logger.warning("Photon geocoding attempt failed", attempt=attempt + 1, error=str(exc))
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2**attempt)

    if data is None:
        logger.error("Photon geocoding failed after all retries", q=q, lang=lang)
        return []

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

    try:
        import redis.asyncio as aioredis

        redis = aioredis.from_url(settings.REDIS_URL)
        try:
            await redis.set(cache_key, json.dumps([s.model_dump() for s in suggestions]), ex=86400)
        finally:
            await redis.aclose()
    except Exception as exc:
        logger.warning("Redis cache write failed", error=str(exc))

    return suggestions
