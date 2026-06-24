from typing import Any
from urllib.parse import quote_plus

_STORES: list[dict[str, str]] = [
    {"name": "Небо", "search_url": "https://nebo.ua/?s={q}"},  # noqa: RUF001
    {"name": "КСД", "search_url": "https://ksd.ua/search?searchstring={q}"},
    {"name": "Буква", "search_url": "https://bukva.ua/search?query={q}"},
    {"name": "Vivat", "search_url": "https://vivat.com.ua/?s={q}"},
    {"name": "Yakaboo", "search_url": "https://www.yakaboo.ua/ua/search/result?q={q}"},
]


def check_stores(title: str) -> list[dict[str, Any]]:
    q = quote_plus(title)
    return [
        {
            "name": store["name"],
            "url": store["search_url"].format(q=q),
            "found": None,
            "product_url": None,
        }
        for store in _STORES
    ]
