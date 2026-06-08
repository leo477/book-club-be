from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.schemas.routes import RoutePoint
from app.services.routing_service import walking_route

_ORIGIN = (50.45, 30.52)
_DEST = (50.46, 30.53)
# Google Routes GeoJSON coordinates are [lng, lat] pairs.
_GEOJSON = {"routes": [{"polyline": {"geoJsonLinestring": {"coordinates": [[30.52, 50.45], [30.53, 50.46]]}}}]}


def _settings(api_key: str = "test-maps-key") -> Settings:
    return Settings.model_construct(MAPS_SERVER_API_KEY=api_key)


def _aiohttp_session(json_data: dict | None = None, raise_exc: Exception | None = None) -> MagicMock:
    """Build a mock for aiohttp.ClientSession supporting the nested async-with usage."""
    response = MagicMock()
    response.raise_for_status = MagicMock(side_effect=raise_exc)
    response.json = AsyncMock(return_value=json_data)

    resp_cm = MagicMock()
    resp_cm.__aenter__ = AsyncMock(return_value=response)
    resp_cm.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post = MagicMock(return_value=resp_cm)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=session)


def _redis() -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    return redis


@pytest.mark.asyncio
async def test_walking_route_no_api_key_returns_empty():
    result = await walking_route(_ORIGIN, _DEST, _settings(api_key=""))
    assert result == []


@pytest.mark.asyncio
async def test_walking_route_cache_hit_skips_http():
    redis = _redis()
    redis.get = AsyncMock(return_value='[{"lat": 1.0, "lng": 2.0}]')

    with patch("app.services.routing_service.aiohttp.ClientSession") as session_cls:
        result = await walking_route(_ORIGIN, _DEST, _settings(), redis=redis)

    assert result == [RoutePoint(lat=1.0, lng=2.0)]
    session_cls.assert_not_called()


@pytest.mark.asyncio
async def test_walking_route_success_parses_and_caches():
    redis = _redis()
    session_cls = _aiohttp_session(json_data=_GEOJSON)

    with patch("app.services.routing_service.aiohttp.ClientSession", session_cls):
        result = await walking_route(_ORIGIN, _DEST, _settings(), redis=redis)

    assert result == [RoutePoint(lat=50.45, lng=30.52), RoutePoint(lat=50.46, lng=30.53)]
    redis.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_walking_route_no_redis_still_fetches():
    session_cls = _aiohttp_session(json_data=_GEOJSON)

    with patch("app.services.routing_service.aiohttp.ClientSession", session_cls):
        result = await walking_route(_ORIGIN, _DEST, _settings())

    assert len(result) == 2


@pytest.mark.asyncio
async def test_walking_route_empty_routes_returns_empty():
    session_cls = _aiohttp_session(json_data={"routes": []})

    with patch("app.services.routing_service.aiohttp.ClientSession", session_cls):
        result = await walking_route(_ORIGIN, _DEST, _settings(), redis=_redis())

    assert result == []


@pytest.mark.asyncio
async def test_walking_route_http_error_returns_empty():
    session_cls = _aiohttp_session(raise_exc=RuntimeError("boom"))

    with patch("app.services.routing_service.aiohttp.ClientSession", session_cls):
        result = await walking_route(_ORIGIN, _DEST, _settings(), redis=_redis())

    assert result == []


@pytest.mark.asyncio
async def test_walking_route_redis_read_failure_falls_through_to_http():
    redis = _redis()
    redis.get = AsyncMock(side_effect=RuntimeError("redis down"))
    session_cls = _aiohttp_session(json_data=_GEOJSON)

    with patch("app.services.routing_service.aiohttp.ClientSession", session_cls):
        result = await walking_route(_ORIGIN, _DEST, _settings(), redis=redis)

    assert len(result) == 2


@pytest.mark.asyncio
async def test_walking_route_redis_write_failure_still_returns_path():
    redis = _redis()
    redis.set = AsyncMock(side_effect=RuntimeError("redis down"))
    session_cls = _aiohttp_session(json_data=_GEOJSON)

    with patch("app.services.routing_service.aiohttp.ClientSession", session_cls):
        result = await walking_route(_ORIGIN, _DEST, _settings(), redis=redis)

    assert len(result) == 2
