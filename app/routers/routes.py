from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Request

from app.config import Settings
from app.dependencies import get_redis, get_settings_dep
from app.limiter import limiter
from app.schemas.routes import WalkingRouteResponse
from app.services.routing_service import walking_route

router = APIRouter(prefix="/api/v1/routes", tags=["routes"])


@router.get("/walking")
@limiter.limit("30/minute")
async def walking(
    request: Request,  # slowapi requires this exact parameter name
    settings: Annotated[Settings, Depends(get_settings_dep)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    origin_lat: Annotated[float, Query(ge=-90, le=90)],
    origin_lng: Annotated[float, Query(ge=-180, le=180)],
    dest_lat: Annotated[float, Query(ge=-90, le=90)],
    dest_lng: Annotated[float, Query(ge=-180, le=180)],
) -> WalkingRouteResponse:
    return WalkingRouteResponse(
        path=await walking_route((origin_lat, origin_lng), (dest_lat, dest_lng), settings, redis=redis)
    )
