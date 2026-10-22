from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func
from sqlalchemy.ext.asyncio import AsyncSession


async def fetch_page_with_total(
    db: AsyncSession,
    stmt: Select[Any],
    count_stmt: Select[Any],
    skip: int,
    limit: int,
) -> tuple[list[tuple[Any, ...]], int]:
    result = await db.execute(stmt.add_columns(func.count().over()).offset(skip).limit(limit))
    rows = result.all()
    if rows:
        return [tuple(row[:-1]) for row in rows], rows[0][-1]
    return [], (await db.execute(count_stmt)).scalar_one()
