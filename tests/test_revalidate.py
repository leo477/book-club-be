import json
import logging
from functools import partial
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import Settings
from app.services import revalidate

EVENT = {"title": "Book Night", "date": "2099-12-31T10:00:00+00:00", "city": "Kyiv", "description": "Read"}


def _settings(url: str = "https://web.test/_internal/revalidate", secret: str = "s3cret") -> Settings:
    return Settings(WEB_REVALIDATE_URL=url, WEB_REVALIDATE_SECRET=secret)


@pytest.fixture
def calls():
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"ok": True})

    real = httpx.AsyncClient
    with patch.object(revalidate.httpx, "AsyncClient", partial(real, transport=httpx.MockTransport(handler))):
        yield recorded


@pytest.mark.asyncio
async def test_notify_posts_tags_with_secret(calls):
    with patch.object(revalidate, "get_settings", return_value=_settings()):
        await revalidate.notify_revalidate(["clubs", "club:1"])
    assert len(calls) == 1
    assert str(calls[0].url) == "https://web.test/_internal/revalidate"
    assert calls[0].headers["X-Revalidate-Secret"] == "s3cret"
    assert json.loads(calls[0].content) == {"tags": ["clubs", "club:1"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "secret"), [("", "x"), ("https://web.test/r", ""), ("", "")])
async def test_notify_disabled_when_unset(calls, url, secret):
    with patch.object(revalidate, "get_settings", return_value=_settings(url, secret)):
        await revalidate.notify_revalidate(["clubs"])
    assert calls == []


@pytest.mark.asyncio
async def test_notify_swallows_errors_without_logging_secret(caplog):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    real = httpx.AsyncClient
    caplog.set_level(logging.DEBUG)
    with (
        patch.object(revalidate, "get_settings", return_value=_settings()),
        patch.object(revalidate.httpx, "AsyncClient", partial(real, transport=httpx.MockTransport(boom))),
    ):
        await revalidate.notify_revalidate(["clubs"])
    assert "s3cret" not in caplog.text


@pytest.mark.asyncio
async def test_club_and_event_mutations_notify(async_client, auth_headers, calls):
    with patch.object(revalidate, "get_settings", return_value=_settings()):
        org = await auth_headers(email="rv@example.com")
        await async_client.patch("/api/v1/users/me/role", headers=org, json={"role": "organizer"})
        created = await async_client.post("/api/v1/clubs", headers=org, json={"name": "RV", "isPublic": True})
        club_id = created.json()["id"]
        assert len(calls) == 1
        assert json.loads(calls[0].content) == {"tags": ["clubs", f"club:{club_id}"]}

        await async_client.patch(f"/api/v1/clubs/{club_id}", headers=org, json={"isPublic": False})
        await async_client.patch(f"/api/v1/clubs/{club_id}/pause", headers=org)
        ev = await async_client.post(f"/api/v1/clubs/{club_id}/events", headers=org, json=EVENT)
        await async_client.patch(f"/api/v1/events/{ev.json()['id']}", headers=org, json={"title": "New"})
        await async_client.patch(f"/api/v1/events/{ev.json()['id']}/cancel", headers=org)
        await async_client.patch(f"/api/v1/clubs/{club_id}/cancel", headers=org)
        await async_client.delete(f"/api/v1/clubs/{club_id}", headers=org)
    assert len(calls) == 8
    assert all(json.loads(c.content)["tags"] == ["clubs", f"club:{club_id}"] for c in calls)


@pytest.mark.asyncio
async def test_no_notify_on_failed_mutation(async_client, auth_headers, calls):
    with patch.object(revalidate, "get_settings", return_value=_settings()):
        other = await auth_headers(email="rv2@example.com")
        resp = await async_client.patch("/api/v1/clubs/00000000-0000-0000-0000-000000000001/pause", headers=other)
    assert resp.status_code == 403
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "5xx"])
async def test_downstream_failure_does_not_affect_response(async_client, auth_headers, failure, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(503)

    real = httpx.AsyncClient
    caplog.set_level(logging.DEBUG)
    with (
        patch.object(revalidate, "get_settings", return_value=_settings()),
        patch.object(revalidate.httpx, "AsyncClient", partial(real, transport=httpx.MockTransport(handler))),
    ):
        org = await auth_headers(email=f"rv-{failure}@example.com")
        await async_client.patch("/api/v1/users/me/role", headers=org, json={"role": "organizer"})
        resp = await async_client.post("/api/v1/clubs", headers=org, json={"name": "RV", "isPublic": True})
    assert resp.status_code == 201
    assert "s3cret" not in caplog.text


@pytest.mark.asyncio
async def test_webhook_runs_after_commit_with_connection_released(async_client, auth_headers, test_engine):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    seen: dict[str, object] = {}
    sessions: list[AsyncSession] = []
    real_rollback = AsyncSession.rollback

    async def spy_rollback(self):
        sessions.append(self)
        await real_rollback(self)

    async def fake_notify(tags: list[str]) -> None:
        # A fresh connection sees the committed value, and the request's session holds no transaction/connection.
        async with test_engine.connect() as conn:
            seen["name"] = (await conn.execute(text("select name from clubs"))).scalar_one()
        seen["in_transaction"] = [s.in_transaction() for s in sessions]
        seen["tags"] = tags

    org = await auth_headers(email="rv-order@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=org, json={"role": "organizer"})
    created = await async_client.post("/api/v1/clubs", headers=org, json={"name": "Old", "isPublic": True})
    club_id = created.json()["id"]
    sessions.clear()
    with (
        patch.object(AsyncSession, "rollback", spy_rollback),
        patch.object(revalidate, "notify_revalidate", fake_notify),
    ):
        resp = await async_client.patch(f"/api/v1/clubs/{club_id}", headers=org, json={"name": "New"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"
    assert seen["name"] == "New"
    assert seen["in_transaction"] and not any(seen["in_transaction"])
    assert seen["tags"] == ["clubs", f"club:{club_id}"]


@pytest.mark.asyncio
async def test_member_moderation_routes_notify(async_client, auth_headers, make_member, register_user):
    notified: list[list[str]] = []

    async def fake_notify(tags: list[str]) -> None:
        notified.append(tags)

    org = await auth_headers(email="rv-mod@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=org, json={"role": "organizer"})
    created = await async_client.post("/api/v1/clubs", headers=org, json={"name": "Mod", "isPublic": True})
    club_id = created.json()["id"]
    member = await auth_headers(email="rv-mod-m@example.com")
    uid = await make_member(club_id, member)
    with patch.object(revalidate, "notify_revalidate", fake_notify):
        role = await async_client.patch(
            f"/api/v1/clubs/{club_id}/members/{uid}/role", headers=org, json={"role": "organizer"}
        )
        ban = await async_client.post(f"/api/v1/clubs/{club_id}/members/{uid}/ban", headers=org, json={"duration": 1})
        unban = await async_client.delete(f"/api/v1/clubs/{club_id}/bans/{uid}", headers=org)
    assert [role.status_code, ban.status_code, unban.status_code] == [200, 201, 204]
    assert len(notified) == 3

    # reject_join_request: needs a club that requires approval and a pending requester.
    org2 = await auth_headers(email="rv-mod-o2@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=org2, json={"role": "organizer"})
    closed = await async_client.post(
        "/api/v1/clubs", headers=org2, json={"name": "Closed", "description": "D", "city": "Kyiv"}
    )
    assert closed.status_code == 201, closed.text
    closed_id = closed.json()["id"]
    await register_user(email="rv-mod-r@example.com")
    requester = await auth_headers(email="rv-mod-r@example.com")
    rid = (await async_client.get("/api/v1/users/me", headers=requester)).json()["id"]
    await async_client.post(f"/api/v1/clubs/{closed_id}/join", headers=requester)
    notified.clear()
    with patch.object(revalidate, "notify_revalidate", fake_notify):
        rej = await async_client.post(f"/api/v1/clubs/{closed_id}/join-requests/{rid}/reject", headers=org2)
    assert rej.status_code == 204
    assert notified == [["clubs", f"club:{closed_id}"]]


@pytest.mark.asyncio
async def test_session_release_failure_does_not_fail_request(async_client, auth_headers):
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.ext.asyncio import AsyncSession

    notified: list[list[str]] = []

    async def fake_notify(tags: list[str]) -> None:
        notified.append(tags)

    org = await auth_headers(email="rv-dead@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=org, json={"role": "organizer"})
    created = await async_client.post("/api/v1/clubs", headers=org, json={"name": "Dead", "isPublic": True})
    club_id = created.json()["id"]
    boom = AsyncMock(side_effect=OperationalError("rollback", {}, Exception("connection dead")))
    with (
        patch.object(AsyncSession, "rollback", boom),
        patch.object(revalidate, "notify_revalidate", fake_notify),
    ):
        resp = await async_client.patch(f"/api/v1/clubs/{club_id}", headers=org, json={"name": "Still OK"})
    assert resp.status_code == 200
    assert notified == [["clubs", f"club:{club_id}"]]


@pytest.mark.asyncio
async def test_hard_timeout_does_not_fail_request(async_client, auth_headers, caplog):
    import asyncio
    import time

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    real = httpx.AsyncClient
    caplog.set_level(logging.DEBUG)
    org = await auth_headers(email="rv-hang@example.com")
    await async_client.patch("/api/v1/users/me/role", headers=org, json={"role": "organizer"})
    with (
        patch.object(revalidate, "get_settings", return_value=_settings()),
        patch.object(revalidate, "_TIMEOUT_SECONDS", 0.3),
        patch.object(revalidate.logger, "warning") as warn,
        patch.object(revalidate.httpx, "AsyncClient", partial(real, transport=httpx.MockTransport(hang))),
    ):
        start = time.monotonic()
        resp = await async_client.post("/api/v1/clubs", headers=org, json={"name": "RV", "isPublic": True})
        elapsed = time.monotonic() - start
    assert resp.status_code == 201
    assert warn.call_args.args[0] == "revalidate notify timed out"
    assert elapsed < 5


@pytest.mark.asyncio
async def test_unconfigured_is_noop_without_http(calls):
    with patch.object(revalidate, "get_settings", return_value=_settings(url="", secret="")):
        await revalidate.revalidate_club("abc")
    assert calls == []
