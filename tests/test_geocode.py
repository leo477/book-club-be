from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from httpx import AsyncClient

from app.config import Settings
from app.dependencies import get_settings_dep
from app.main import app

FAKE_PHOTON_RESPONSE = {
    "features": [
        {
            "properties": {"name": "Kyiv", "city": "Kyiv", "country": "Ukraine"},
            "geometry": {"coordinates": [30.5, 50.4]},
        }
    ]
}


def _make_aiohttp_mock(json_data: dict, method: str = "get") -> MagicMock:
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value=json_data)

    mock_ctx_response = AsyncMock()
    mock_ctx_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_ctx_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    setattr(mock_session, method, MagicMock(return_value=mock_ctx_response))

    mock_ctx_session = AsyncMock()
    mock_ctx_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx_session.__aexit__ = AsyncMock(return_value=False)

    return mock_ctx_session


@pytest.mark.asyncio
async def test_autocomplete_returns_suggestions(async_client: AsyncClient) -> None:
    mock_aiohttp_session = _make_aiohttp_mock(FAKE_PHOTON_RESPONSE)

    with patch("aiohttp.ClientSession", return_value=mock_aiohttp_session):
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
    """When Redis raises an error, geocoding should still work via Photon."""
    from app.dependencies import get_redis
    from app.main import app

    mock_redis_failing = AsyncMock()
    mock_redis_failing.get = AsyncMock(side_effect=ConnectionError("Redis unavailable"))
    mock_redis_failing.set = AsyncMock()

    async def _failing_redis():
        return mock_redis_failing

    app.dependency_overrides[get_redis] = _failing_redis

    mock_aiohttp_session = _make_aiohttp_mock(FAKE_PHOTON_RESPONSE)

    from unittest.mock import AsyncMock as _AsyncMock

    _mock = _AsyncMock()
    _mock.get = _AsyncMock(return_value=None)
    _mock.set = _AsyncMock()

    async def _default_redis():
        return _mock

    try:
        with patch("aiohttp.ClientSession", return_value=mock_aiohttp_session):
            response = await async_client.get("/api/v1/geocode/autocomplete?q=Kyiv")
    finally:
        app.dependency_overrides[get_redis] = _default_redis

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0


@pytest.mark.asyncio
async def test_autocomplete_photon_error_returns_502(async_client: AsyncClient) -> None:
    mock_ctx_response = AsyncMock()
    mock_ctx_response.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("connection failed"))
    mock_ctx_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_ctx_response)

    mock_ctx_session = AsyncMock()
    mock_ctx_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_ctx_session):
        response = await async_client.get("/api/v1/geocode/autocomplete?q=Kyiv")

    assert response.status_code == 502


FAKE_GOOGLE_AUTOCOMPLETE_RESPONSE = {
    "suggestions": [
        {
            "placePrediction": {
                "placeId": "ChIJBUVa4U7P0UARJ1P3MyBHQ7s",
                "structuredFormat": {
                    "mainText": {"text": "Kyiv"},
                    "secondaryText": {"text": "Ukraine"},
                },
            }
        }
    ]
}

FAKE_GOOGLE_PLACE_DETAILS_RESPONSE = {
    "id": "ChIJBUVa4U7P0UARJ1P3MyBHQ7s",
    "formattedAddress": "Kyiv, Ukraine",
    "location": {"latitude": 50.4501, "longitude": 30.5234},
    "addressComponents": [
        {"longText": "Kyiv", "types": ["locality"]},
        {"longText": "Ukraine", "types": ["country"]},
    ],
}


def _make_aiohttp_post_mock(json_data: dict) -> MagicMock:
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value=json_data)

    mock_ctx_response = AsyncMock()
    mock_ctx_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_ctx_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_ctx_response)

    mock_ctx_session = AsyncMock()
    mock_ctx_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx_session.__aexit__ = AsyncMock(return_value=False)

    return mock_ctx_session


def _settings_with_maps_key(key: str = "test-maps-api-key") -> Settings:
    return Settings.model_construct(
        ENV="test",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        SECRET_KEY="",
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        REFRESH_TOKEN_EXPIRE_DAYS=7,
        ALLOWED_ORIGINS=["http://localhost:4200"],
        REDIS_URL="redis://localhost:6379",
        SENTRY_DSN="",
        LOG_LEVEL="INFO",
        SUPABASE_URL="https://test.supabase.co",
        SUPABASE_ANON_KEY="test-anon-key",
        MAPS_API_KEY=key,
    )


@pytest.mark.asyncio
async def test_autocomplete_google_places_with_session_token(async_client: AsyncClient) -> None:
    settings = _settings_with_maps_key()
    app.dependency_overrides[get_settings_dep] = lambda: settings

    mock_aiohttp_session = _make_aiohttp_post_mock(FAKE_GOOGLE_AUTOCOMPLETE_RESPONSE)

    try:
        with patch("aiohttp.ClientSession", return_value=mock_aiohttp_session):
            response = await async_client.get("/api/v1/geocode/autocomplete?q=Kyiv&session_token=test-token-123")
    finally:
        del app.dependency_overrides[get_settings_dep]

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["label"] == "Kyiv, Ukraine"
    assert data[0]["place_id"] == "ChIJBUVa4U7P0UARJ1P3MyBHQ7s"


@pytest.mark.asyncio
async def test_autocomplete_google_places_api_error(async_client: AsyncClient) -> None:
    settings = _settings_with_maps_key()
    app.dependency_overrides[get_settings_dep] = lambda: settings

    mock_ctx_response = AsyncMock()
    mock_ctx_response.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("connection failed"))
    mock_ctx_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_ctx_response)

    mock_ctx_session = AsyncMock()
    mock_ctx_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx_session.__aexit__ = AsyncMock(return_value=False)

    try:
        with patch("aiohttp.ClientSession", return_value=mock_ctx_session):
            response = await async_client.get("/api/v1/geocode/autocomplete?q=Kyiv&session_token=test-token-123")
    finally:
        del app.dependency_overrides[get_settings_dep]

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_place_details_returns_suggestion(async_client: AsyncClient) -> None:
    settings = _settings_with_maps_key()
    app.dependency_overrides[get_settings_dep] = lambda: settings

    mock_aiohttp_session = _make_aiohttp_mock(FAKE_GOOGLE_PLACE_DETAILS_RESPONSE)

    try:
        with patch("aiohttp.ClientSession", return_value=mock_aiohttp_session):
            response = await async_client.get(
                "/api/v1/geocode/place-details?place_id=ChIJBUVa4U7P0UARJ1P3MyBHQ7s&session_token=test-token-123"
            )
    finally:
        del app.dependency_overrides[get_settings_dep]

    assert response.status_code == 200
    data = response.json()
    assert data["label"] == "Kyiv, Ukraine"
    assert data["city"] == "Kyiv"
    assert data["country"] == "Ukraine"
    assert data["lat"] == pytest.approx(50.4501)
    assert data["lng"] == pytest.approx(30.5234)


@pytest.mark.asyncio
async def test_place_details_maps_not_configured(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/v1/geocode/place-details?place_id=some-id&session_token=tok")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_place_details_api_error(async_client: AsyncClient) -> None:
    settings = _settings_with_maps_key()
    app.dependency_overrides[get_settings_dep] = lambda: settings

    mock_ctx_response = AsyncMock()
    mock_ctx_response.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("timeout"))
    mock_ctx_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_ctx_response)

    mock_ctx_session = AsyncMock()
    mock_ctx_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx_session.__aexit__ = AsyncMock(return_value=False)

    try:
        with patch("aiohttp.ClientSession", return_value=mock_ctx_session):
            response = await async_client.get(
                "/api/v1/geocode/place-details?place_id=ChIJBUVa4U7P0UARJ1P3MyBHQ7s&session_token=test-token-123"
            )
    finally:
        del app.dependency_overrides[get_settings_dep]

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_get_maps_key(async_client: AsyncClient) -> None:
    settings = _settings_with_maps_key("my-api-key-abc")
    app.dependency_overrides[get_settings_dep] = lambda: settings

    try:
        response = await async_client.get("/api/v1/config/maps-key")
    finally:
        del app.dependency_overrides[get_settings_dep]

    assert response.status_code == 200
    assert response.json() == {"mapsApiKey": "my-api-key-abc"}
