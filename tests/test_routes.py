import pytest


@pytest.mark.asyncio
async def test_walking_route_endpoint_returns_path(async_client):
    # Test settings have no MAPS_SERVER_API_KEY, so walking_route returns an empty path.
    resp = await async_client.get(
        "/api/v1/routes/walking",
        params={"origin_lat": 50.45, "origin_lng": 30.52, "dest_lat": 50.46, "dest_lng": 30.53},
    )
    assert resp.status_code == 200
    assert resp.json() == {"path": []}


@pytest.mark.asyncio
async def test_walking_route_endpoint_validates_bounds(async_client):
    resp = await async_client.get(
        "/api/v1/routes/walking",
        params={"origin_lat": 999, "origin_lng": 30.52, "dest_lat": 50.46, "dest_lng": 30.53},
    )
    assert resp.status_code == 422
