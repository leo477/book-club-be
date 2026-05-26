import asyncio
import logging
import time
from typing import Annotated
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.dependencies import get_current_user
from app.models.user import User

router = APIRouter(prefix="/api/v1/books", tags=["books"])
logger = logging.getLogger(__name__)

_STORES = [
    {"name": "Небо", "search": "https://nebo.ua/search?q={q}"},  # noqa: RUF001
    {"name": "КСД", "search": "https://ksd.com.ua/search?query={q}"},
    {"name": "Букава", "search": "https://bukva.ua/search?q={q}"},
    {"name": "Vivat", "search": "https://vivat-publish.com/ua/search/?q={q}"},
    {"name": "Yakaboo", "search": "https://www.yakaboo.ua/catalogsearch/result/?q={q}"},
]

_cache: dict[str, tuple[list, float]] = {}
_CACHE_TTL = 3600


class StoreResult(BaseModel):
    name: str
    url: str
    found: bool


async def _check_store(client: httpx.AsyncClient, store: dict, title: str) -> StoreResult:
    q = quote(title)
    url = store["search"].format(q=q)
    try:
        resp = await client.get(url, follow_redirects=True, timeout=8.0)
        text = resp.text.lower()
        no_results = any(kw in text for kw in [
            "нічого не знайдено", "не знайдено", "0 товарів", "товарів не знайдено",
            "nothing found", "no results", "0 results", "no products",
        ])
        found = resp.status_code == 200 and not no_results
    except Exception as exc:
        logger.warning("Store check failed for %s: %s", store["name"], exc)
        found = False
    return StoreResult(name=store["name"], url=url, found=found)


@router.get("/stores", response_model=list[StoreResult])
async def get_book_stores(
    title: str,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> list[StoreResult]:
    now = time.time()
    cached = _cache.get(title)
    if cached and now < cached[1]:
        return cached[0]

    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 BookClub/1.0"}) as client:
        results = await asyncio.gather(*[_check_store(client, s, title) for s in _STORES])

    result_list = list(results)
    _cache[title] = (result_list, now + _CACHE_TTL)
    return result_list
