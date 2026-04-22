from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from httpx import AsyncClient

FAKE_PHOTON_RESPONSE = {
    "features": [
        {
            "properties": {"name": "Kyiv", "city": "Kyiv", "country": "Ukraine"},
            "geometry": {"coordinates": [30.5, 50.4]},
        }
    ]
}


def _make_aiohttp_mock(json_data: dict) -> MagicMock:
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value=json_data)

    mock_ctx_response = AsyncMock()
    mock_ctx_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_ctx_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_ctx_response)

    mock_ctx_session = AsyncMock()
    mock_ctx_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx_session.__aexit__ = AsyncMock(return_value=False)

    return mock_ctx_session


def _make_redis_mock(cached_value=None) -> MagicMock:
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=cached_value)
    mock_redis.set = AsyncMock()
    mock_redis.aclose = AsyncMock()

    mock_from_url = MagicMock(return_value=mock_redis)
    return mock_from_url


@pytest.mark.asyncio
async def test_autocomplete_returns_suggestions(async_client: AsyncClient) -> None:
    mock_redis_from_url = _make_redis_mock(cached_value=None)
    mock_aiohttp_session = _make_aiohttp_mock(FAKE_PHOTON_RESPONSE)

    with (
        patch("redis.asyncio.from_url", mock_redis_from_url),
        patch("aiohttp.ClientSession", return_value=mock_aiohttp_session),
    ):
        response = await async_client.get("/api/v1/geocode/autocomplete?q=Kyiv")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert "Kyiv" in data[0]["label"]
    assert data[0]["lat"] == 50.4
    assert data[0]["lng"] == 30.5


@pytest.mark.asyncio
async def test_autocomplete_q_too_short(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/v1/geocode/autocomplete?q=K")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_autocomplete_redis_unavailable_returns_results(async_client: AsyncClient) -> None:
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(side_effect=ConnectionError("Redis unavailable"))
    mock_redis.aclose = AsyncMock()
    mock_redis_from_url = MagicMock(return_value=mock_redis)

    mock_aiohttp_session = _make_aiohttp_mock(FAKE_PHOTON_RESPONSE)

    with (
        patch("redis.asyncio.from_url", mock_redis_from_url),
        patch("aiohttp.ClientSession", return_value=mock_aiohttp_session),
    ):
        response = await async_client.get("/api/v1/geocode/autocomplete?q=Kyiv")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0


@pytest.mark.asyncio
async def test_autocomplete_photon_error_returns_502(async_client: AsyncClient) -> None:
    mock_redis_from_url = _make_redis_mock(cached_value=None)

    mock_ctx_response = AsyncMock()
    mock_ctx_response.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("connection failed"))
    mock_ctx_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_ctx_response)

    mock_ctx_session = AsyncMock()
    mock_ctx_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("redis.asyncio.from_url", mock_redis_from_url),
        patch("aiohttp.ClientSession", return_value=mock_ctx_session),
    ):
        response = await async_client.get("/api/v1/geocode/autocomplete?q=Kyiv")

    assert response.status_code == 502
