import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.csp_report import CspReport
from app.services import csp_report as svc
from app.tasks.cleanup import cleanup_csp_reports, run_csp_report_cleanup_pass

URL = "/api/v1/analytics/csp-report"
DOC = "http://localhost:4200/clubs/3f2b8c1e-1111-4222-8333-444455556666/events/123456?tab=x#frag"
REPORTS_JSON = {"content-type": "application/reports+json"}


@pytest.fixture(autouse=True)
def _reset_budget():
    svc.reset_budget()
    yield
    svc.reset_budget()


def _tt(**body):
    base = {
        "documentURL": DOC,
        "effectiveDirective": "require-trusted-types-for",
        "blockedURL": "trusted-types-sink",
        "sample": "Element innerHTML|<img src=x onerror=alert(1)>",
        "disposition": "report",
    }
    return {"type": "csp-violation", "url": DOC, "body": {**base, **body}}


async def _rows(test_engine):
    async with AsyncSession(test_engine) as db:
        return (await db.execute(select(CspReport))).scalars().all()


async def _post(client, payload, headers=REPORTS_JSON):
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return await client.post(URL, content=data, headers=headers)


@pytest.mark.asyncio
async def test_reporting_api_array_of_two(async_client, test_engine):
    resp = await _post(
        async_client, [_tt(), _tt(effectiveDirective="script-src-elem", blockedURL="https://x.cdn.io/a.js?k=1")]
    )
    assert resp.status_code == 204
    rows = {r.directive: r for r in await _rows(test_engine)}
    assert set(rows) == {"require-trusted-types-for", "script-src-elem"}
    tt = rows["require-trusted-types-for"]
    assert (tt.env, tt.sink, tt.disposition, tt.count) == ("dev", "Element innerHTML", "report", 1)
    assert rows["script-src-elem"].blocked == "other"


@pytest.mark.asyncio
async def test_legacy_csp_report_content_type(async_client, test_engine):
    body = {
        "csp-report": {
            "document-uri": DOC,
            "effective-directive": "trusted-types",
            "blocked-uri": "trusted-types-policy",
            "script-sample": "Creating a TrustedTypePolicy named 'angular#bundler' violates",
            "disposition": "enforce",
        }
    }
    assert (await _post(async_client, body, {"content-type": "application/csp-report"})).status_code == 204
    (row,) = await _rows(test_engine)
    assert (row.directive, row.blocked, row.policy_name, row.disposition) == (
        "trusted-types",
        "trusted-types-policy",
        "other",
        "enforce",
    )


@pytest.mark.asyncio
async def test_plain_json_single_object(async_client, test_engine):
    assert (await _post(async_client, _tt(), {"content-type": "application/json"})).status_code == 204
    assert len(await _rows(test_engine)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("junk", [b"not json", b"[1, 2]", b'{"body": 5}', b"null", b""])
async def test_junk_body_is_204_and_stores_nothing(async_client, test_engine, junk):
    assert (await _post(async_client, junk)).status_code == 204
    assert await _rows(test_engine) == []


@pytest.mark.asyncio
async def test_oversized_declared_and_actual_are_204_and_stored_nothing(async_client, test_engine):
    big = json.dumps([_tt(sample="x" * 9000)]).encode()
    assert (await _post(async_client, big)).status_code == 204
    assert (await _post(async_client, _tt(), {**REPORTS_JSON, "content-length": "99999"})).status_code == 204

    async def chunks():
        yield big[:5000]
        yield big[5000:]

    resp = await async_client.post(URL, content=chunks(), headers=REPORTS_JSON)
    assert resp.status_code == 204
    assert await _rows(test_engine) == []


@pytest.mark.asyncio
async def test_only_first_ten_reports_are_used(async_client, test_engine):
    sinks = sorted(svc._SINKS)[:15]
    reports = [_tt(sample=f"{sink}|x") for sink in sinks]
    assert (await _post(async_client, reports)).status_code == 204
    assert len(await _rows(test_engine)) == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "doc",
    [
        "https://evil.example.com/x",
        "https://book-club-planer.vercel.app.evil.com/",
        "http://127.0.0.1:4200/",
        "ftp://localhost/",
    ],
)
async def test_foreign_host_ignored(async_client, test_engine, doc):
    assert (await _post(async_client, [_tt(documentURL=doc)])).status_code == 204
    assert await _rows(test_engine) == []


@pytest.mark.asyncio
async def test_preview_host_accepted_as_preview(async_client, test_engine):
    doc = "https://book-club-fe.vercel.app/"
    assert (await _post(async_client, [_tt(documentURL=doc)])).status_code == 204
    (row,) = await _rows(test_engine)
    assert (row.env, row.route) == ("preview", "/")


@pytest.mark.asyncio
async def test_query_fragment_stripped_and_ids_normalised(async_client, test_engine):
    body = _tt(
        blockedURL="https://cdn.example.com/p/a.js?token=secret#h",
        sample="see https://a.b/c?q=1 mail me@x.io " + "z" * 200,
    )
    assert (await _post(async_client, [body])).status_code == 204
    (row,) = await _rows(test_engine)
    assert row.route == "/clubs/:id/*"
    assert row.blocked == "other"
    dump = f"{row.route}{row.blocked}{row.sample}"
    assert not any(s in dump for s in ("token", "secret", "frag", "tab=x", "me@x.io", "q=1"))
    assert len(row.sample) == 120
    assert "[url]" in row.sample and "[email]" in row.sample


@pytest.mark.asyncio
async def test_same_key_twice_increments_count(async_client, test_engine):
    assert (await _post(async_client, [_tt(), _tt()])).status_code == 204
    assert (await _post(async_client, [_tt(documentURL=DOC.replace("123456", "654321"))])).status_code == 204
    (row,) = await _rows(test_engine)
    assert row.count == 3


@pytest.mark.asyncio
async def test_different_key_makes_new_row(async_client, test_engine):
    await _post(async_client, [_tt(), _tt(sample="Element outerHTML|x")])
    assert len(await _rows(test_engine)) == 2


@pytest.mark.asyncio
async def test_hourly_cap_then_silent_204(async_client, test_engine, monkeypatch):
    monkeypatch.setattr(svc, "HOURLY_UPSERT_CAP", 3)
    for i in range(3):
        assert (
            await _post(async_client, [_tt(effectiveDirective=("img-src", "font-src", "media-src")[i])])
        ).status_code == 204
    assert (await _post(async_client, [_tt(effectiveDirective="frame-src")])).status_code == 204
    assert len(await _rows(test_engine)) == 3


@pytest.mark.asyncio
async def test_rate_limited_after_30_per_minute(async_client):
    for _ in range(30):
        assert (await _post(async_client, b"x")).status_code == 204
    assert (await _post(async_client, b"x")).status_code == 429


def test_table_has_no_identifying_columns():
    assert set(CspReport.__table__.columns.keys()) == {
        "id", "created_at", "day", "env", "route", "directive", "disposition",
        "sink", "blocked", "policy_name", "sample", "count",
    }  # fmt: skip


@pytest.mark.asyncio
async def test_not_blocked_by_csrf_with_session_cookie_and_no_origin(async_client, test_engine):
    resp = await async_client.post(
        URL,
        content=json.dumps([_tt()]).encode(),
        headers=REPORTS_JSON,
        cookies={"access_token": "stale", "refresh_token": "stale"},
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_cleanup_deletes_only_rows_older_than_30_days(test_engine):
    now = datetime.now(UTC)
    key = {"env": "dev", "route": "/", "directive": "d", "disposition": "report"}
    async with AsyncSession(test_engine) as db:
        db.add_all(
            [
                CspReport(day=(now - timedelta(days=31)).date(), **key),
                CspReport(day=(now - timedelta(days=29)).date(), **key),
            ]
        )
        await db.commit()
        assert await run_csp_report_cleanup_pass(db, now=now) == 1
        assert len((await db.execute(select(CspReport))).scalars().all()) == 1


def _stop_after(n):
    calls = 0

    async def fake_sleep(_):
        nonlocal calls
        calls += 1
        if calls > n:
            raise asyncio.CancelledError

    return fake_sleep


@pytest.mark.asyncio
async def test_cleanup_loop_runs_pass_then_propagates_cancellation(monkeypatch):
    session = AsyncMock()
    cm = MagicMock()
    cm.__aenter__.return_value = session
    cm.__aexit__.return_value = False
    monkeypatch.setattr("app.database.AsyncSessionLocal", MagicMock(return_value=cm))
    monkeypatch.setattr("app.tasks.cleanup.asyncio.sleep", _stop_after(1))
    run_pass = AsyncMock(return_value=2)
    monkeypatch.setattr("app.tasks.cleanup.run_csp_report_cleanup_pass", run_pass)
    with pytest.raises(asyncio.CancelledError):
        await cleanup_csp_reports()
    run_pass.assert_awaited_with(session)
    assert run_pass.await_count == 2  # pass first, then sleep: a pass happens before the first sleep


@pytest.mark.asyncio
async def test_cleanup_loop_survives_errors(monkeypatch):
    monkeypatch.setattr("app.database.AsyncSessionLocal", MagicMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr("app.tasks.cleanup.asyncio.sleep", _stop_after(2))
    with pytest.raises(asyncio.CancelledError):
        await cleanup_csp_reports()


@pytest.mark.asyncio
async def test_slow_body_times_out_with_204_and_no_row(async_client, test_engine, monkeypatch):
    monkeypatch.setattr("app.routers.analytics._CSP_BODY_READ_TIMEOUT_SECONDS", 0.2)

    async def slow_body():
        yield b"["
        await asyncio.sleep(5)
        yield json.dumps(_tt()).encode() + b"]"

    resp = await async_client.post(URL, content=slow_body(), headers=REPORTS_JSON)
    assert resp.status_code == 204
    assert await _rows(test_engine) == []


@pytest.mark.asyncio
async def test_db_error_is_swallowed_with_204(async_client, monkeypatch):
    async def boom(*_):
        raise SQLAlchemyError("down")

    monkeypatch.setattr(svc, "store", boom)
    assert (await _post(async_client, [_tt()])).status_code == 204


async def _post_in_batches(client, bodies):
    for i in range(0, len(bodies), 10):  # 10 reports per request keeps under the 30/minute limit
        assert (await _post(client, bodies[i : i + 10])).status_code == 204


@pytest.mark.asyncio
async def test_forged_route_directive_sample_stay_bounded(async_client, test_engine):
    bodies = []
    for i in range(200):
        top = ["clubs", f"zzz{i}", "events"][i % 3]
        bodies.append(
            _tt(
                documentURL=f"http://localhost:4200/{top}/s{i}/x{i}/y",
                effectiveDirective=f"fake-{i}",
                sample=f"Function|{i}" if i % 2 else f"junk {i}",
            )
        )
    await _post_in_batches(async_client, bodies)
    rows = await _rows(test_engine)
    assert len(rows) <= 6
    assert {r.directive for r in rows} == {"other"}
    assert {r.route for r in rows} == {"/other", "/clubs/:x/*", "/events/:x/*"}


@pytest.mark.asyncio
async def test_forged_blocked_hosts_collapse_to_other(async_client, test_engine):
    await _post_in_batches(async_client, [_tt(blockedURL=f"https://h{i}.evil.com/p") for i in range(290)])
    assert {r.blocked for r in await _rows(test_engine)} == {"other"}


@pytest.mark.asyncio
async def test_routes_directive_case_and_blocked_bounds(async_client, test_engine):
    docs = ["/", "/clubs/3f2b8c1e-1111-4222-8333-444455556666/chat", "/events/new", "/nope/1", "/profile/edit/deep"]
    await _post(
        async_client, [_tt(documentURL=f"http://localhost:4200{d}", effectiveDirective="Script-Src-Elem") for d in docs]
    )
    await _post(async_client, [_tt(blockedURL="https://" + "x" * 300 + ".com/p"), _tt(blockedURL="data:text/plain,hi")])
    rows = await _rows(test_engine)
    assert {r.route for r in rows} == {"/", "/clubs/:id/*", "/events/new", "/other", "/profile/edit/*"}
    assert {r.directive for r in rows} >= {"script-src-elem"}
    assert {r.blocked for r in rows} == {"trusted-types-sink", "other", "data"}


@pytest.mark.asyncio
async def test_real_chrome_shapes_are_distinct_rows(async_client, test_engine):
    shapes = [
        _tt(sample="HTMLScriptElement src|https://x/_next/static/chunks/a.js"),
        _tt(sample="Function|(...)"),
        _tt(),
        _tt(effectiveDirective="script-src", blockedURL="eval", sample="", disposition="enforce"),
    ]
    assert (await _post(async_client, shapes)).status_code == 204
    rows = await _rows(test_engine)
    assert {(r.directive, r.sink, r.blocked, r.disposition) for r in rows} == {
        ("require-trusted-types-for", "HTMLScriptElement src", "trusted-types-sink", "report"),
        ("require-trusted-types-for", "Function", "trusted-types-sink", "report"),
        ("require-trusted-types-for", "Element innerHTML", "trusted-types-sink", "report"),
        ("script-src", "", "eval", "enforce"),
    }


def test_blocked_keeps_known_hosts_and_maps_own_host_to_self():
    cfg = Settings(SUPABASE_URL="https://abc.supabase.co", BACKEND_URL="https://api.example.com")

    def blocked(url):
        return svc.normalise(_tt(blockedURL=url), cfg)["blocked"]

    assert blocked("https://abc.supabase.co/storage/v1/x.png") == "abc.supabase.co"
    assert blocked("https://api.example.com/api/v1/x") == "api.example.com"
    assert blocked("https://maps.googleapis.com/maps/api/js") == "maps.googleapis.com"
    assert blocked("https://covers.openlibrary.org/b/id/1.jpg") == "covers.openlibrary.org"
    assert blocked("http://localhost:4200/assets/a.js") == "self"
    assert blocked("https://evil.example.net/a.js") == "other"


@pytest.mark.asyncio
async def test_forged_sinks_and_policy_names_collapse_to_other(async_client, test_engine):
    bodies = [_tt(sample=f"Element fake{i}|x") for i in range(100)]
    bodies += [_tt(sample=f"Function|x violates policy named 'evil{i}'") for i in range(100)]
    await _post_in_batches(async_client, bodies)
    rows = await _rows(test_engine)
    assert {(r.sink, r.policy_name) for r in rows} == {("other", ""), ("Function", "other")}


@pytest.mark.asyncio
async def test_real_sinks_and_policies_stay_distinct(async_client, test_engine):
    shapes = [
        _tt(sample="HTMLScriptElement src|https://x/a.js"),
        _tt(sample="Function|(...)"),
        _tt(sample="Element innerHTML|<b>"),
        _tt(sample="Element innerHTML|<b> named 'lit-html'"),
        _tt(sample="Element innerHTML|<b> named 'angular'"),
    ]
    assert (await _post(async_client, shapes)).status_code == 204
    got = {(r.sink, r.policy_name) for r in await _rows(test_engine)}
    assert got == {
        ("HTMLScriptElement src", ""),
        ("Function", ""),
        ("Element innerHTML", ""),
        ("Element innerHTML", "lit-html"),
        ("Element innerHTML", "angular"),
    }


def test_scheme_only_blocked_values():
    cfg = Settings()

    def blocked(url):
        return svc.normalise(_tt(blockedURL=url), cfg)["blocked"]

    assert blocked("data") == "data"
    assert blocked("blob") == "blob"
    assert blocked("filesystem") == "filesystem"
    assert blocked("data:image/png;base64,AAAA") == "data"
    assert blocked("blob:https://evil.com/uuid") == "blob"
    assert blocked("about") == "other"
    assert blocked("chrome-extension://abc/x.js") == "other"
    assert blocked("") == ""
