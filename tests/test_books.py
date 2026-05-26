from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.routers.books as books_module


def _make_http_response(status_code: int = 200, text: str = "book page content") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def _make_mock_client(response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


@pytest.fixture(autouse=True)
def clear_cache():
    books_module._cache.clear()
    yield
    books_module._cache.clear()


@pytest.mark.asyncio
async def test_get_stores_unauthenticated(async_client):
    resp = await async_client.get("/api/v1/books/stores?title=Test")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_stores_found(async_client, register_user, auth_headers):
    await register_user(email="books_found@example.com")
    headers = await auth_headers(email="books_found@example.com")

    mock_resp = _make_http_response(200, "great book available here")
    mock_client = _make_mock_client(mock_resp)

    with patch("app.routers.books.httpx.AsyncClient", return_value=mock_client):
        resp = await async_client.get("/api/v1/books/stores?title=Kobzar", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 5
    assert all(item["found"] is True for item in data)
    assert all("url" in item and "name" in item for item in data)


@pytest.mark.asyncio
async def test_get_stores_not_found_english_keyword(async_client, register_user, auth_headers):
    await register_user(email="books_noresult@example.com")
    headers = await auth_headers(email="books_noresult@example.com")

    mock_resp = _make_http_response(200, "sorry, no results found for your query")
    mock_client = _make_mock_client(mock_resp)

    with patch("app.routers.books.httpx.AsyncClient", return_value=mock_client):
        resp = await async_client.get("/api/v1/books/stores?title=NonExistentBook", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert all(item["found"] is False for item in data)


@pytest.mark.asyncio
async def test_get_stores_not_found_ukrainian_keyword(async_client, register_user, auth_headers):
    await register_user(email="books_ua_noresult@example.com")
    headers = await auth_headers(email="books_ua_noresult@example.com")

    mock_resp = _make_http_response(200, "нічого не знайдено за вашим запитом")
    mock_client = _make_mock_client(mock_resp)

    with patch("app.routers.books.httpx.AsyncClient", return_value=mock_client):
        resp = await async_client.get("/api/v1/books/stores?title=НеіснуючаКнига", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert all(item["found"] is False for item in data)


@pytest.mark.asyncio
async def test_get_stores_http_error_falls_back_to_not_found(async_client, register_user, auth_headers):
    await register_user(email="books_error@example.com")
    headers = await auth_headers(email="books_error@example.com")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.routers.books.httpx.AsyncClient", return_value=mock_client):
        resp = await async_client.get("/api/v1/books/stores?title=AnyBook", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert all(item["found"] is False for item in data)


@pytest.mark.asyncio
async def test_get_stores_cache_hit(async_client, register_user, auth_headers):
    await register_user(email="books_cache@example.com")
    headers = await auth_headers(email="books_cache@example.com")

    mock_resp = _make_http_response(200, "book found here")
    mock_client = _make_mock_client(mock_resp)

    with patch("app.routers.books.httpx.AsyncClient", return_value=mock_client):
        resp1 = await async_client.get("/api/v1/books/stores?title=CachedBook", headers=headers)
        assert resp1.status_code == 200

    # Second call — httpx must NOT be called again (served from cache)
    with patch("app.routers.books.httpx.AsyncClient", return_value=mock_client) as patched:
        resp2 = await async_client.get("/api/v1/books/stores?title=CachedBook", headers=headers)
        patched.assert_not_called()

    assert resp2.status_code == 200
    assert resp2.json() == resp1.json()


@pytest.mark.asyncio
async def test_get_stores_cache_expired(async_client, register_user, auth_headers):
    import time

    await register_user(email="books_expired@example.com")
    headers = await auth_headers(email="books_expired@example.com")

    from app.routers.books import StoreResult

    # Seed an already-expired cache entry
    stale = [StoreResult(name="Fake", url="http://x", found=False)]
    books_module._cache["ExpiredBook"] = (stale, time.time() - 1)

    mock_resp = _make_http_response(200, "fresh content")
    mock_client = _make_mock_client(mock_resp)

    with patch("app.routers.books.httpx.AsyncClient", return_value=mock_client) as patched:
        resp = await async_client.get("/api/v1/books/stores?title=ExpiredBook", headers=headers)
        patched.assert_called_once()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 5
