from __future__ import annotations

import json
from typing import TYPE_CHECKING

import aiohttp
import structlog

if TYPE_CHECKING:
    import redis.asyncio as aioredis

from app.config import Settings
from app.schemas.routes import RoutePoint

logger = structlog.get_logger(__name__)


async def walking_route(
    origin: tuple[float, float],
    dest: tuple[float, float],
    settings: Settings,
    redis: aioredis.Redis | None = None,
) -> list[RoutePoint]:
    if not settings.MAPS_SERVER_API_KEY:
        return []

    olat, olng = origin
    dlat, dlng = dest
    cache_key = f"route:walk:{olat:.5f},{olng:.5f}:{dlat:.5f},{dlng:.5f}"

    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached:
                data = json.loads(cached)
                return [RoutePoint(**item) for item in data]
        except Exception as exc:
            logger.warning("Redis cache read failed", error=str(exc))

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://routes.googleapis.com/directions/v2:computeRoutes",
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": settings.MAPS_SERVER_API_KEY,
                    "X-Goog-FieldMask": "routes.polyline.geoJsonLinestring",
                },
                json={
                    "origin": {"location": {"latLng": {"latitude": olat, "longitude": olng}}},
                    "destination": {"location": {"latLng": {"latitude": dlat, "longitude": dlng}}},
                    "travelMode": "WALK",
                    "polylineEncoding": "GEO_JSON_LINESTRING",
                },
            ) as response:
                response.raise_for_status()
                data = await response.json()

        routes = data.get("routes", [])
        if not routes:
            return []
        # GeoJSON coordinates are [lng, lat] pairs.
        coordinates = routes[0]["polyline"]["geoJsonLinestring"]["coordinates"]
        path = [RoutePoint(lat=coord[1], lng=coord[0]) for coord in coordinates]
    except Exception as exc:
        # Return [] instead of raising 502: the frontend falls back to a straight line.
        logger.error("Google Routes request failed", error=str(exc), exc_type=type(exc).__name__)
        return []

    if redis is not None:
        try:
            await redis.set(cache_key, json.dumps([p.model_dump() for p in path]), ex=86400)
        except Exception as exc:
            logger.warning("Redis cache write failed", error=str(exc))

    return path
