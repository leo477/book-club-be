"""Postgres-only regression tests (SCRUM-137).

The rest of the suite runs on SQLite, which does not enforce enum types or casts,
so e.g. ``Event.status == 'upcoming'`` passed there but 500'd on Postgres (SCRUM-133).
These tests run against a real Postgres when ``TEST_POSTGRES_URL`` is set
(``postgresql+asyncpg://user:pass@host:5432/db``) and are skipped otherwise.
The schema is built with ``alembic upgrade head``, so migrations are exercised too.
The test engine does not use ``ssl=require`` (that lives in app/database.py and is untouched).
"""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Enum, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.dependencies import get_db_dep, require_club_organizer
from app.main import app
from app.models.club import Club
from app.models.event import Event
from app.models.user import User
from app.repositories.club import ClubRepository
from tests._pg_safety import check_pg_test_url

pytestmark = pytest.mark.postgres

PG_URL = os.environ.get("TEST_POSTGRES_URL", "")
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def migrated_pg() -> str:
    reason = check_pg_test_url(PG_URL)
    if reason:
        pytest.fail(f"Refusing to run destructive Postgres tests against TEST_POSTGRES_URL: {reason}", pytrace=False)
    env = {**os.environ, "DATABASE_URL": PG_URL}
    # Console script, not `python -m alembic`: the repo's alembic/ dir shadows the package.
    subprocess.run(  # noqa: S603  # nosec B603
        [str(Path(sys.executable).parent / "alembic"), "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        timeout=300,
    )
    return PG_URL


async def _reset(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE events, club_members, clubs, users CASCADE"))


@pytest_asyncio.fixture
async def pg_engine(migrated_pg):
    engine = create_async_engine(migrated_pg, connect_args={"statement_cache_size": 0})
    await _reset(engine)  # an aborted earlier run must not leave stale rows
    yield engine
    await _reset(engine)
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_session(pg_engine):
    async with async_sessionmaker(pg_engine, expire_on_commit=False)() as session:
        yield session


async def _seed_club(session: AsyncSession) -> Club:
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com", display_name="Org", role="organizer")
    session.add(user)
    await session.flush()
    club = Club(id=uuid.uuid4(), name="PG club", organizer_id=user.id, city="Kyiv")
    session.add(club)
    await session.flush()
    return club


def _event(club: Club, status: str, delta: timedelta) -> Event:
    return Event(
        id=uuid.uuid4(),
        club_id=club.id,
        title=f"e-{status}",
        date=datetime.now(UTC) + delta,
        city="Kyiv",
        status=status,
    )


async def test_count_upcoming_events(pg_session):
    club = await _seed_club(pg_session)
    future = timedelta(days=5)
    pg_session.add_all(
        [
            _event(club, "scheduled", future),
            _event(club, "active", future),
            _event(club, "rescheduled", future),
            _event(club, "held", future),  # not upcoming
            _event(club, "cancelled", future),  # not upcoming
            _event(club, "scheduled", -future),  # in the past
        ]
    )
    await pg_session.commit()

    count = await ClubRepository(pg_session).count_upcoming_events(club.id, datetime.now(UTC))
    assert count == 3


async def test_club_stats_endpoint(pg_engine, pg_session):
    club = await _seed_club(pg_session)
    pg_session.add(_event(club, "scheduled", timedelta(days=2)))
    await pg_session.commit()

    sessionmaker = async_sessionmaker(pg_engine, expire_on_commit=False)

    async def _db():
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db_dep] = _db
    app.dependency_overrides[require_club_organizer] = lambda: None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/api/v1/clubs/{club.id}/stats")
    finally:
        app.dependency_overrides.pop(get_db_dep, None)
        app.dependency_overrides.pop(require_club_organizer, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["upcomingEventsCount"] == 1
    assert data["totalEvents"] == 1


async def test_model_enums_match_database_and_literals_are_valid(pg_session):
    """Every model Enum column: DB enum labels == model values, and `col == value` compiles/runs on Postgres."""
    checked = 0
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if not isinstance(col.type, Enum):
                continue
            db_labels = (
                (await pg_session.execute(text(f"SELECT unnest(enum_range(NULL::{col.type.name}))::text")))
                .scalars()
                .all()
            )
            assert set(db_labels) == set(col.type.enums), f"{table.name}.{col.name} drifted from migrations"
            for value in col.type.enums:
                await pg_session.execute(select(func.count()).select_from(table).where(col == value))
                checked += 1
    assert checked > 0


@pytest.mark.parametrize("status", ["scheduled", "active", "held", "cancelled", "rescheduled"])
async def test_event_status_values_insert(pg_session, status):
    club = await _seed_club(pg_session)
    pg_session.add(_event(club, status, timedelta(days=1)))
    await pg_session.commit()
    stored = (await pg_session.execute(select(Event.status).where(Event.club_id == club.id))).scalar_one()
    assert stored == status


async def test_invalid_event_status_literal_rejected(pg_session):
    """The SCRUM-133 failure mode: an unknown enum literal must error on Postgres (guards the guard)."""
    with pytest.raises(Exception, match="upcoming"):
        await pg_session.execute(select(func.count(Event.id)).where(Event.status == "upcoming"))
