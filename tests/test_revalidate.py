import json
import logging
from functools import partial
from unittest.mock import patch

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
