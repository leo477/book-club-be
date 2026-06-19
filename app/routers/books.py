import asyncio
import time
from typing import Annotated
from urllib.parse import quote

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.config import get_settings
from app.dependencies import get_current_user
from app.models.user import User
from app.services import google_books_service

settings = get_settings()

router = APIRouter(prefix="/api/v1/books", tags=["books"])
logger = structlog.get_logger(__name__)

_STORES = [
    {"name": "Небо", "search": "https://nebo.ua/search?q={q}"},  # noqa: RUF001
    {"name": "КСД", "search": "https://ksd.com.ua/search?query={q}"},
    {"name": "Букава", "search": "https://bukva.ua/search?q={q}"},
    {"name": "Vivat", "search": "https://vivat-publish.com/ua/search/?q={q}"},
    {"name": "Yakaboo", "search": "https://www.yakaboo.ua/catalogsearch/result/?q={q}"},
]

_CACHE_TTL = 3600
_CACHE_MAX_SIZE = 1000

# Realistic browser headers reduce trivial bot-blocking (403/503) from the
# storefronts, which otherwise made every store resolve to found=False.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
}

_NO_RESULTS_KEYWORDS = [
    "нічого не знайдено",
    "не знайдено",
    "0 товарів",
    "товарів не знайдено",
    "результатів не знайдено",
    "nothing found",
    "no results",
    "0 results",
    "no products",
    "no matches",
]


class BookSuggestion(BaseModel):
    id: str
    title: str
    authors: list[str]
    description: str | None = None
    thumbnail: str | None = None
    publishedDate: str | None = None
    publisher: str | None = None


class BookDetails(BookSuggestion):
    pass


class StoreResult(BaseModel):
    name: str
    url: str
    found: bool


_cache: dict[str, tuple[list[StoreResult], float]] = {}


async def _check_store(client: httpx.AsyncClient, store: dict[str, str], title: str) -> StoreResult:
    q = quote(title)
    url = store["search"].format(q=q)
    try:
        resp = await client.get(url, follow_redirects=True, timeout=8.0)
        # Non-200 (403/503/Cloudflare bot-walls, etc.): availability is unknown,
        # report not-found but still hand back a working search URL.
        if resp.status_code != 200:
            logger.info("Store %s returned status %s", store["name"], resp.status_code)
            return StoreResult(name=store["name"], url=url, found=False)
        text = resp.text.lower()
        no_results = any(kw in text for kw in _NO_RESULTS_KEYWORDS)
        found = not no_results
    except Exception as exc:
        logger.warning("Store check failed for %s: %s", store["name"], exc)
        found = False
    return StoreResult(name=store["name"], url=url, found=found)


@router.get("/search")
async def search_books(
    q: Annotated[str, Query(min_length=2)],
    _current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> list[BookSuggestion]:
    results = await google_books_service.search_books(q, limit, settings.GOOGLE_BOOKS_API_KEY)
    return [BookSuggestion.model_validate(item) for item in results]


@router.get("/details/{book_id}", responses={404: {"description": "Book not found"}})
async def get_book_details(
    book_id: str,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> BookDetails:
    item = await google_books_service.get_book_by_id(book_id, settings.GOOGLE_BOOKS_API_KEY)
    if item is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return BookDetails.model_validate(item)


@router.get("/stores")
async def get_book_stores(
    title: str,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> list[StoreResult]:
    now = time.time()
    cached = _cache.get(title)
    if cached and now < cached[1]:
        return cached[0]

    async with httpx.AsyncClient(headers=_BROWSER_HEADERS) as client:
        results = await asyncio.gather(*[_check_store(client, s, title) for s in _STORES])

    result_list = list(results)
    if len(_cache) >= _CACHE_MAX_SIZE:
        for k in [k for k, (_, exp) in _cache.items() if exp <= now]:
            del _cache[k]
        if len(_cache) >= _CACHE_MAX_SIZE:
            del _cache[next(iter(_cache))]
    _cache[title] = (result_list, now + _CACHE_TTL)
    return result_list
