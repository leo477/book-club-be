import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routers import books as books_module
from app.services import google_books_service as gbs_module


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
    gbs_module.CACHE.clear()
    yield
    books_module._cache.clear()
    gbs_module.CACHE.clear()


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

    # Seed an already-expired cache entry
    stale = [books_module.StoreResult(name="Fake", url="http://x", found=False)]
    books_module._cache["ExpiredBook"] = (stale, time.time() - 1)

    mock_resp = _make_http_response(200, "fresh content")
    mock_client = _make_mock_client(mock_resp)

    with patch("app.routers.books.httpx.AsyncClient", return_value=mock_client) as patched:
        resp = await async_client.get("/api/v1/books/stores?title=ExpiredBook", headers=headers)
        patched.assert_called_once()

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 5


# ---------------------------------------------------------------------------
# GET /api/v1/books/search
# ---------------------------------------------------------------------------


def _make_gbs_response(items: list[dict] | None = None, status_code: int = 200) -> MagicMock:
    """Build a fake httpx response for Google Books API calls."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"items": items} if items is not None else {}
    resp.raise_for_status = MagicMock()
    return resp


def _make_gbs_client(response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


_SAMPLE_VOLUME_ITEM = {
    "id": "abc123",
    "volumeInfo": {
        "title": "Test Book",
        "authors": ["Author One"],
        "description": "A great book about testing.",
        "imageLinks": {"thumbnail": "http://example.com/thumb.jpg"},
        "publishedDate": "2020-01-01",
        "publisher": "Pub House",
    },
}


@pytest.mark.asyncio
async def test_search_books_unauthenticated(async_client):
    resp = await async_client.get("/api/v1/books/search?q=python")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_search_books_query_too_short(async_client, register_user, auth_headers):
    await register_user(email="srch_short@example.com")
    headers = await auth_headers(email="srch_short@example.com")
    resp = await async_client.get("/api/v1/books/search?q=a", headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_books_returns_results(async_client, register_user, auth_headers):
    await register_user(email="srch_ok@example.com")
    headers = await auth_headers(email="srch_ok@example.com")

    gbs_resp = _make_gbs_response(items=[_SAMPLE_VOLUME_ITEM])
    gbs_client = _make_gbs_client(gbs_resp)

    with patch("app.services.google_books_service.httpx.AsyncClient", return_value=gbs_client):
        resp = await async_client.get("/api/v1/books/search?q=python", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "abc123"
    assert data[0]["title"] == "Test Book"
    assert data[0]["authors"] == ["Author One"]
    # thumbnail http → https upgrade
    assert data[0]["thumbnail"].startswith("https://")


@pytest.mark.asyncio
async def test_search_books_empty_results(async_client, register_user, auth_headers):
    await register_user(email="srch_empty@example.com")
    headers = await auth_headers(email="srch_empty@example.com")

    gbs_resp = _make_gbs_response(items=None)
    gbs_client = _make_gbs_client(gbs_resp)

    with patch("app.services.google_books_service.httpx.AsyncClient", return_value=gbs_client):
        resp = await async_client.get("/api/v1/books/search?q=xyznotfound", headers=headers)

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_search_books_cache_hit(async_client, register_user, auth_headers):
    """Second identical search is served from cache without calling the API."""
    await register_user(email="srch_cache@example.com")
    headers = await auth_headers(email="srch_cache@example.com")

    gbs_resp = _make_gbs_response(items=[_SAMPLE_VOLUME_ITEM])
    gbs_client = _make_gbs_client(gbs_resp)

    with patch("app.services.google_books_service.httpx.AsyncClient", return_value=gbs_client):
        resp1 = await async_client.get("/api/v1/books/search?q=cachetest", headers=headers)
    assert resp1.status_code == 200

    with patch("app.services.google_books_service.httpx.AsyncClient", return_value=gbs_client) as patched:
        resp2 = await async_client.get("/api/v1/books/search?q=cachetest", headers=headers)
        patched.assert_not_called()

    assert resp2.status_code == 200
    assert resp2.json() == resp1.json()


# ---------------------------------------------------------------------------
# GET /api/v1/books/details/{book_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_book_details_unauthenticated(async_client):
    resp = await async_client.get("/api/v1/books/details/abc123")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_book_details_found(async_client, register_user, auth_headers):
    await register_user(email="details_ok@example.com")
    headers = await auth_headers(email="details_ok@example.com")

    gbs_resp = MagicMock()
    gbs_resp.status_code = 200
    gbs_resp.json.return_value = _SAMPLE_VOLUME_ITEM
    gbs_resp.raise_for_status = MagicMock()
    gbs_client = _make_gbs_client(gbs_resp)

    with patch("app.services.google_books_service.httpx.AsyncClient", return_value=gbs_client):
        resp = await async_client.get("/api/v1/books/details/abc123", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "abc123"
    assert data["title"] == "Test Book"
    assert data["publisher"] == "Pub House"


@pytest.mark.asyncio
async def test_get_book_details_not_found_from_google(async_client, register_user, auth_headers):
    """When Google Books returns 404 the endpoint returns 404."""
    await register_user(email="details_nf@example.com")
    headers = await auth_headers(email="details_nf@example.com")

    gbs_resp = MagicMock()
    gbs_resp.status_code = 404
    gbs_resp.raise_for_status = MagicMock()
    gbs_client = _make_gbs_client(gbs_resp)

    with patch("app.services.google_books_service.httpx.AsyncClient", return_value=gbs_client):
        resp = await async_client.get("/api/v1/books/details/abc123", headers=headers)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_book_details_invalid_id(async_client, register_user, auth_headers):
    """A book_id that fails the regex check is rejected → 404 (service returns None)."""
    await register_user(email="details_invalid@example.com")
    headers = await auth_headers(email="details_invalid@example.com")

    # book_id with only valid chars but triggers regex rejection via service returning None
    resp = await async_client.get("/api/v1/books/details/a" * 65, headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_book_details_cache_hit(async_client, register_user, auth_headers):
    """Second call for same book_id is served from cache."""
    await register_user(email="details_cache@example.com")
    headers = await auth_headers(email="details_cache@example.com")

    gbs_resp = MagicMock()
    gbs_resp.status_code = 200
    gbs_resp.json.return_value = _SAMPLE_VOLUME_ITEM
    gbs_resp.raise_for_status = MagicMock()
    gbs_client = _make_gbs_client(gbs_resp)

    with patch("app.services.google_books_service.httpx.AsyncClient", return_value=gbs_client):
        resp1 = await async_client.get("/api/v1/books/details/abc123", headers=headers)
    assert resp1.status_code == 200

    with patch("app.services.google_books_service.httpx.AsyncClient", return_value=gbs_client) as patched:
        resp2 = await async_client.get("/api/v1/books/details/abc123", headers=headers)
        patched.assert_not_called()

    assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# google_books_service unit tests (_map_item edge cases)
# ---------------------------------------------------------------------------


def test_map_item_all_fields_present():
    item = {
        "id": "xyz789",
        "volumeInfo": {
            "title": "Full Book",
            "authors": ["A", "B"],
            "description": "Short desc",
            "imageLinks": {"thumbnail": "http://img.example.com/t.jpg"},
            "publishedDate": "2021-05-10",
            "publisher": "My Publisher",
        },
    }
    result = gbs_module._map_item(item)
    assert result["id"] == "xyz789"
    assert result["title"] == "Full Book"
    assert result["authors"] == ["A", "B"]
    assert result["thumbnail"] == "https://img.example.com/t.jpg"
    assert result["description"] == "Short desc"
    assert result["publishedDate"] == "2021-05-10"
    assert result["publisher"] == "My Publisher"


def test_map_item_missing_volume_info():
    """Item with no volumeInfo returns safe defaults."""
    result = gbs_module._map_item({"id": "nvi"})
    assert result["id"] == "nvi"
    assert result["title"] == ""
    assert result["authors"] == []
    assert result["thumbnail"] is None
    assert result["description"] is None


def test_map_item_small_thumbnail_fallback():
    """Falls back to smallThumbnail when thumbnail is absent."""
    item = {
        "id": "st1",
        "volumeInfo": {
            "imageLinks": {"smallThumbnail": "http://small.example.com/s.jpg"},
        },
    }
    result = gbs_module._map_item(item)
    assert result["thumbnail"] == "https://small.example.com/s.jpg"


def test_map_item_description_truncated_at_800():
    """Descriptions longer than 800 chars are truncated."""
    long_desc = "x" * 1000
    item = {
        "id": "trunc",
        "volumeInfo": {"description": long_desc},
    }
    result = gbs_module._map_item(item)
    assert len(result["description"]) == 800


def test_map_item_missing_id_returns_empty_string():
    """When item has no 'id' key the result id is empty string, not None."""
    result = gbs_module._map_item({"volumeInfo": {"title": "No ID Book"}})
    assert result["id"] == ""


def test_map_item_empty_image_links():
    """Empty imageLinks dict yields None thumbnail."""
    item = {"id": "noimg", "volumeInfo": {"imageLinks": {}}}
    result = gbs_module._map_item(item)
    assert result["thumbnail"] is None


@pytest.mark.asyncio
async def test_search_books_service_caches_result():
    """search_books stores result in CACHE and reuses it on second call."""
    gbs_resp = MagicMock()
    gbs_resp.json.return_value = {"items": [_SAMPLE_VOLUME_ITEM]}
    gbs_resp.raise_for_status = MagicMock()
    gbs_client = AsyncMock()
    gbs_client.get = AsyncMock(return_value=gbs_resp)
    gbs_client.__aenter__ = AsyncMock(return_value=gbs_client)
    gbs_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.google_books_service.httpx.AsyncClient", return_value=gbs_client):
        result1 = await gbs_module.search_books("python", limit=3)
    assert len(result1) == 1

    # Second call — should come from cache, not trigger another HTTP request
    with patch("app.services.google_books_service.httpx.AsyncClient", return_value=gbs_client) as patched:
        result2 = await gbs_module.search_books("python", limit=3)
        patched.assert_not_called()
    assert result1 == result2


@pytest.mark.asyncio
async def test_get_book_by_id_invalid_id_returns_none():
    """_BOOK_ID_RE rejects IDs that contain invalid characters."""
    result = await gbs_module.get_book_by_id("bad id/with slash")
    assert result is None


@pytest.mark.asyncio
async def test_get_book_by_id_empty_response_returns_none():
    """When Google returns a body with no 'id' field, service returns None."""
    gbs_resp = MagicMock()
    gbs_resp.status_code = 200
    gbs_resp.json.return_value = {}
    gbs_resp.raise_for_status = MagicMock()
    gbs_client = AsyncMock()
    gbs_client.get = AsyncMock(return_value=gbs_resp)
    gbs_client.__aenter__ = AsyncMock(return_value=gbs_client)
    gbs_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.google_books_service.httpx.AsyncClient", return_value=gbs_client):
        result = await gbs_module.get_book_by_id("validId123")
    assert result is None


@pytest.mark.asyncio
async def test_get_book_by_id_404_returns_none():
    """HTTP 404 from Google Books → service returns None."""
    gbs_resp = MagicMock()
    gbs_resp.status_code = 404
    gbs_resp.raise_for_status = MagicMock()
    gbs_client = AsyncMock()
    gbs_client.get = AsyncMock(return_value=gbs_resp)
    gbs_client.__aenter__ = AsyncMock(return_value=gbs_client)
    gbs_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.google_books_service.httpx.AsyncClient", return_value=gbs_client):
        result = await gbs_module.get_book_by_id("validId123")
    assert result is None


@pytest.mark.asyncio
async def test_get_book_by_id_happy_path_and_caches():
    """Valid response is mapped and cached."""
    gbs_resp = MagicMock()
    gbs_resp.status_code = 200
    gbs_resp.json.return_value = _SAMPLE_VOLUME_ITEM
    gbs_resp.raise_for_status = MagicMock()
    gbs_client = AsyncMock()
    gbs_client.get = AsyncMock(return_value=gbs_resp)
    gbs_client.__aenter__ = AsyncMock(return_value=gbs_client)
    gbs_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.google_books_service.httpx.AsyncClient", return_value=gbs_client):
        result = await gbs_module.get_book_by_id("abc123")

    assert result is not None
    assert result["id"] == "abc123"
    assert result["title"] == "Test Book"

    # Verify it's cached
    assert "id:abc123" in gbs_module.CACHE

    # Second call — no HTTP
    with patch("app.services.google_books_service.httpx.AsyncClient", return_value=gbs_client) as patched:
        result2 = await gbs_module.get_book_by_id("abc123")
        patched.assert_not_called()
    assert result2 == result


def test_google_books_cache_set_evicts_oldest_when_full(monkeypatch):
    gbs_module.CACHE.clear()
    monkeypatch.setattr(gbs_module, "CACHE_MAX_SIZE", 3)
    future = time.time() + 1000
    for i in range(3):
        gbs_module.CACHE[f"k{i}"] = ([], future)

    gbs_module._cache_set("new", ([], future))

    assert "k0" not in gbs_module.CACHE  # oldest evicted
    assert "new" in gbs_module.CACHE
    assert len(gbs_module.CACHE) == 3


def test_google_books_cache_set_drops_expired_when_full(monkeypatch):
    gbs_module.CACHE.clear()
    monkeypatch.setattr(gbs_module, "CACHE_MAX_SIZE", 3)
    future = time.time() + 1000
    past = time.time() - 1000
    gbs_module.CACHE["expired1"] = ([], past)
    gbs_module.CACHE["expired2"] = ([], past)
    gbs_module.CACHE["fresh"] = ([], future)

    gbs_module._cache_set("new", ([], future))

    assert "expired1" not in gbs_module.CACHE
    assert "expired2" not in gbs_module.CACHE
    assert "fresh" in gbs_module.CACHE
    assert "new" in gbs_module.CACHE


@pytest.mark.asyncio
async def test_get_stores_cache_eviction(async_client, register_user, auth_headers, monkeypatch):
    await register_user(email="books_evict@example.com")
    headers = await auth_headers(email="books_evict@example.com")

    monkeypatch.setattr(books_module, "_CACHE_MAX_SIZE", 2)
    past = time.time() - 1000
    future = time.time() + 1000
    # 3 entries (> max): the expired one is dropped first, then the oldest fresh one.
    books_module._cache["expired"] = ([], past)
    books_module._cache["fresh1"] = ([], future)
    books_module._cache["fresh2"] = ([], future)

    mock_client = _make_mock_client(_make_http_response(200, "book found here"))
    with patch("app.routers.books.httpx.AsyncClient", return_value=mock_client):
        resp = await async_client.get("/api/v1/books/stores?title=EvictBook", headers=headers)

    assert resp.status_code == 200
    assert "expired" not in books_module._cache  # expired entry dropped
    assert "fresh1" not in books_module._cache  # oldest fresh entry evicted
    assert "EvictBook" in books_module._cache
