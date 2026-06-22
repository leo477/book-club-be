import asyncio
import time
from typing import Any
from urllib.parse import quote_plus, urlsplit

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Values are (payload, expiry_ts) where payload is a single store-result dict.
CACHE: dict[str, tuple[Any, float]] = {}
CACHE_TTL = 3600
CACHE_MAX_SIZE = 1000

_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"

_STORES: list[dict[str, str]] = [
    {"name": "Небо", "domain": "nebo.ua"},  # noqa: RUF001
    {"name": "КСД", "domain": "ksd.ua"},
    {"name": "Буква", "domain": "bukva.ua"},
    {"name": "Vivat", "domain": "vivat.com.ua"},
    {"name": "Yakaboo", "domain": "yakaboo.ua"},
]

# Path/query fragments that mark a search or category listing rather than a product page.
_LISTING_MARKERS = ("/search", "/catalog", "/category", "/catalogsearch", "?q=", "?query=", "?s=")


def _cache_set(key: str, entry: tuple[Any, float]) -> None:
    """Insert into CACHE with a bounded size: drop expired entries, then the oldest if still full."""
    if len(CACHE) >= CACHE_MAX_SIZE:
        now = time.time()
        for k in [k for k, (_, exp) in CACHE.items() if exp <= now]:
            del CACHE[k]
        if len(CACHE) >= CACHE_MAX_SIZE:
            del CACHE[next(iter(CACHE))]
    CACHE[key] = entry


def _fallback_url(title: str, domain: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(title)}+site:{domain}"


def _is_product_link(link: str, domain: str) -> bool:
    parts = urlsplit(link)
    host = parts.netloc.lower()
    if host != domain and not host.endswith(f".{domain}"):
        return False
    if not parts.path or parts.path == "/":
        return False
    haystack = f"{parts.path}?{parts.query}".lower()
    return not any(marker in haystack for marker in _LISTING_MARKERS)


def _degraded(name: str, title: str, domain: str) -> dict[str, Any]:
    return {"name": name, "url": _fallback_url(title, domain), "found": None, "product_url": None}


async def _check_store(
    client: httpx.AsyncClient,
    store: dict[str, str],
    title: str,
    api_key: str,
    cse_id: str,
) -> dict[str, Any]:
    name, domain = store["name"], store["domain"]
    cache_key = f"{title}:{name}"
    now = time.time()
    cached = CACHE.get(cache_key)
    if cached and now < cached[1]:
        return cached[0]  # type: ignore[no-any-return]

    try:
        resp = await client.get(
            _CSE_ENDPOINT,
            params={
                "key": api_key,
                "cx": cse_id,
                "q": title,
                "siteSearch": domain,
                "siteSearchFilter": "i",
                "num": 3,
            },
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("store_search_request_failed", store=name, error=str(exc))
        return _degraded(name, title, domain)

    if resp.status_code == 429:
        logger.warning("store_search_rate_limited", store=name)
        return _degraded(name, title, domain)
    if resp.status_code != 200:
        logger.warning("store_search_http_error", store=name, status=resp.status_code)
        return _degraded(name, title, domain)

    data: dict[str, Any] = resp.json()
    items: list[dict[str, Any]] = data.get("items") or []
    if not items:
        logger.warning("store_search_no_items", store=name)
        return _degraded(name, title, domain)

    for item in items:
        link = item.get("link") or ""
        if _is_product_link(link, domain):
            result = {"name": name, "url": link, "found": True, "product_url": link}
            _cache_set(cache_key, (result, now + CACHE_TTL))
            return result

    result = {"name": name, "url": _fallback_url(title, domain), "found": False, "product_url": None}
    _cache_set(cache_key, (result, now + CACHE_TTL))
    return result


async def check_stores(title: str, api_key: str, cse_id: str) -> list[dict[str, Any]]:
    if not api_key or not cse_id:
        logger.warning("store_search_not_configured")
        return [_degraded(s["name"], title, s["domain"]) for s in _STORES]

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[_check_store(client, store, title, api_key, cse_id) for store in _STORES])
    return list(results)
