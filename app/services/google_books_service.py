import re
import time
from typing import Any
from urllib.parse import quote

import httpx

# Values are (payload, expiry_ts) where payload is list[dict] for search
# and dict for single-item lookups — use Any to avoid a union cache type.
CACHE: dict[str, tuple[Any, float]] = {}
CACHE_TTL = 3600

_FIELDS = "items(id,volumeInfo(title,authors,description,imageLinks,publishedDate,publisher))"
_BOOK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _map_item(item: dict[str, Any]) -> dict[str, Any]:
    info: dict[str, Any] = item.get("volumeInfo") or {}
    image_links: dict[str, Any] = info.get("imageLinks") or {}
    thumbnail: str | None = image_links.get("thumbnail") or image_links.get("smallThumbnail")
    if thumbnail:
        thumbnail = thumbnail.replace("http://", "https://")  # NOSONAR(python:S5332)
    description: str | None = info.get("description")
    if description:
        description = description[:800]
    return {
        "id": item.get("id") or "",
        "title": info.get("title") or "",
        "authors": info.get("authors") or [],
        "description": description,
        "thumbnail": thumbnail,
        "publishedDate": info.get("publishedDate"),
        "publisher": info.get("publisher"),
    }


async def search_books(query: str, limit: int = 5, api_key: str = "") -> list[dict[str, Any]]:
    cache_key = f"search:{query}:{limit}"
    now = time.time()
    cached = CACHE.get(cache_key)
    if cached and now < cached[1]:
        return cached[0]  # type: ignore[no-any-return]

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": query, "maxResults": limit, "key": api_key, "fields": _FIELDS},
            timeout=10.0,
        )
        resp.raise_for_status()

    data: dict[str, Any] = resp.json()
    result: list[dict[str, Any]] = [_map_item(item) for item in data.get("items") or []]
    CACHE[cache_key] = (result, now + CACHE_TTL)
    return result


async def get_book_by_id(book_id: str, api_key: str = "") -> dict[str, Any] | None:
    if not _BOOK_ID_RE.fullmatch(book_id):
        return None

    cache_key = f"id:{book_id}"
    now = time.time()
    cached = CACHE.get(cache_key)
    if cached and now < cached[1]:
        return cached[0]  # type: ignore[no-any-return]

    safe_book_id = quote(book_id, safe="")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://www.googleapis.com/books/v1/volumes/{safe_book_id}",
            params={"key": api_key},
            timeout=10.0,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

    item: dict[str, Any] = resp.json()
    if not item or "id" not in item:
        return None
    result = _map_item(item)
    CACHE[cache_key] = (result, now + CACHE_TTL)
    return result
