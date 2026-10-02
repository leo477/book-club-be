from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.analytics_event import AnalyticsEvent
from app.tasks.cleanup import run_analytics_cleanup_pass

URL = "/api/v1/analytics/event"


async def _rows(db):
    return (await db.execute(select(AnalyticsEvent))).scalars().all()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"app": "angular", "name": "cohort", "bucket": "0-9"},
        {"app": "next", "name": "join_club", "bucket": "90-99"},
        {"app": "next", "name": "js_error", "kind": "boundary", "message": "boom"},
    ],
)
async def test_event_accepted_and_stored(async_client, test_engine, payload):
    resp = await async_client.post(URL, json=payload)
    assert resp.status_code == 204
    assert resp.content == b""

    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(test_engine) as db:
        rows = await _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert (row.app, row.name, row.bucket, row.kind, row.message) == (
        payload["app"],
        payload["name"],
        payload.get("bucket"),
        payload.get("kind"),
        payload.get("message"),
    )
    assert row.created_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"app": "react", "name": "cohort", "bucket": "0-9"},
        {"app": "next", "name": "click"},
        {"app": "next", "name": "cohort", "bucket": "abc"},
        {"app": "next", "name": "cohort", "bucket": "100-199"},
        {"app": "next", "name": "js_error", "message": "x" * 121},
        {"app": "next", "name": "js_error", "kind": "other"},
        {"app": "next", "name": "cohort", "kind": "error"},
        {"app": "next", "name": "join_club", "message": "hi"},
        {"app": "next", "name": "cohort", "user_id": "1"},
    ],
)
async def test_invalid_payload_rejected(async_client, payload):
    assert (await async_client.post(URL, json=payload)).status_code == 422


@pytest.mark.asyncio
async def test_oversized_body_rejected(async_client):
    resp = await async_client.post(URL, json={"app": "next", "name": "js_error", "message": "x" * 3000})
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_rate_limited_after_60_per_minute(async_client):
    payload = {"app": "angular", "name": "cohort", "bucket": "0-9"}
    for _ in range(60):
        assert (await async_client.post(URL, json=payload)).status_code == 204
    assert (await async_client.post(URL, json=payload)).status_code == 429


def test_table_has_no_pii_columns():
    assert set(AnalyticsEvent.__table__.columns.keys()) == {
        "id",
        "created_at",
        "app",
        "name",
        "bucket",
        "kind",
        "message",
    }


@pytest.mark.asyncio
async def test_cleanup_deletes_only_events_older_than_90_days(test_engine):
    from sqlalchemy.ext.asyncio import AsyncSession

    now = datetime.now(UTC)
    async with AsyncSession(test_engine) as db:
        db.add_all(
            [
                AnalyticsEvent(app="next", name="cohort", created_at=now - timedelta(days=91)),
                AnalyticsEvent(app="next", name="cohort", created_at=now - timedelta(days=89)),
            ]
        )
        await db.commit()
        assert await run_analytics_cleanup_pass(db, now=now) == 1
        assert len(await _rows(db)) == 1
