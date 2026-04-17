import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_ok(async_client: AsyncClient) -> None:
    response = await async_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "env" in data


@pytest.mark.asyncio
async def test_health_sync(client) -> None:
    response = async_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
