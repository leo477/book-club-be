import time

import httpx

CACHE: dict[str, tuple] = {}
CACHE_TTL = 3600

_FIELDS = "items(id,volumeInfo(title,authors,description,imageLinks,publishedDate,publisher))"


def _map_item(item: dict) -> dict:
    info = item.get("volumeInfo", {})
    image_links = info.get("imageLinks", {})
    thumbnail = image_links.get("thumbnail") or image_links.get("smallThumbnail")
    if thumbnail:
        thumbnail = thumbnail.replace("http://", "https://")
    description = info.get("description")
    if description:
        description = description[:800]
    return {
        "id": item.get("id", ""),
        "title": info.get("title", ""),
        "authors": info.get("authors") or [],
        "description": description,
        "thumbnail": thumbnail,
        "publishedDate": info.get("publishedDate"),
        "publisher": info.get("publisher"),
    }


async def search_books(query: str, limit: int = 5, api_key: str = "") -> list[dict]:
    cache_key = f"search:{query}:{limit}"
    now = time.time()
    cached = CACHE.get(cache_key)
    if cached and now < cached[1]:
        return cached[0]

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": query, "maxResults": limit, "key": api_key, "fields": _FIELDS},
            timeout=10.0,
        )
        resp.raise_for_status()

    data = resp.json()
    result = [_map_item(item) for item in data.get("items") or []]
    CACHE[cache_key] = (result, now + CACHE_TTL)
    return result


async def get_book_by_id(book_id: str, api_key: str = "") -> dict | None:
    cache_key = f"id:{book_id}"
    now = time.time()
    cached = CACHE.get(cache_key)
    if cached and now < cached[1]:
        return cached[0]

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://www.googleapis.com/books/v1/volumes/{book_id}",
            params={"key": api_key},
            timeout=10.0,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

    item = resp.json()
    if not item or "id" not in item:
        return None
    result = _map_item(item)
    CACHE[cache_key] = (result, now + CACHE_TTL)
    return result
