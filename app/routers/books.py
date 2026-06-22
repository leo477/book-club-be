from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.config import get_settings
from app.dependencies import get_current_user
from app.models.user import User
from app.services import book_stores_service, google_books_service

settings = get_settings()

router = APIRouter(prefix="/api/v1/books", tags=["books"])
logger = structlog.get_logger(__name__)


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
    found: bool | None
    product_url: str | None = None


@router.get("/search", response_model=list[BookSuggestion])
async def search_books(
    q: Annotated[str, Query(min_length=2)],
    _current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> list[BookSuggestion]:
    results = await google_books_service.search_books(q, limit, settings.GOOGLE_BOOKS_API_KEY)
    return [BookSuggestion.model_validate(item) for item in results]


@router.get("/details/{book_id}", response_model=BookDetails, responses={404: {"description": "Book not found"}})
async def get_book_details(
    book_id: str,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> BookDetails:
    item = await google_books_service.get_book_by_id(book_id, settings.GOOGLE_BOOKS_API_KEY)
    if item is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return BookDetails.model_validate(item)


@router.get("/stores", response_model=list[StoreResult])
async def get_book_stores(
    title: str,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> list[StoreResult]:
    results = await book_stores_service.check_stores(
        title, settings.GOOGLE_CSE_API_KEY, settings.GOOGLE_CSE_ID
    )
    return [StoreResult.model_validate(item) for item in results]
