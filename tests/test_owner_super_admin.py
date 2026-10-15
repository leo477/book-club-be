"""Club owner (club.organizer_id) is the super-admin; other organizers are ordinary admins."""

import pytest


async def _setup(async_client, register_user, auth_headers, make_member, tag):
    """Owner + co-organizer (org2) + second co-organizer (org3) + plain member."""
    users = {}
    for name in ("owner", "org2", "org3", "member"):
        email = f"{tag}_{name}@example.com"
        r = await register_user(email=email)  # register is rate limited (5/min): reuse its token
        users[name] = {"h": {"Authorization": f"Bearer {r.json()['accessToken']}"}}
    await async_client.patch("/api/v1/users/me/role", headers=users["owner"]["h"], json={"role": "organizer"})
    resp = await async_client.post(
        "/api/v1/clubs", headers=users["owner"]["h"], json={"name": f"C {tag}", "description": "d", "city": "Kyiv"}
    )
    club_id = resp.json()["id"]
    users["owner"]["id"] = (await async_client.get("/api/v1/users/me", headers=users["owner"]["h"])).json()["id"]
    users["org2"]["id"] = await make_member(club_id, users["org2"]["h"], role="organizer")
    users["org3"]["id"] = await make_member(club_id, users["org3"]["h"], role="organizer")
    users["member"]["id"] = await make_member(club_id, users["member"]["h"])
    return club_id, users


def _url(club_id, uid, suffix=""):
    return f"/api/v1/clubs/{club_id}/members/{uid}{suffix}"


# actor -> target organizer: only owner allowed
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,actor,expected",
    [
        ("kick", "owner", 204),
        ("kick", "org2", 403),
        ("ban", "owner", 201),
        ("ban", "org2", 403),
        ("demote", "owner", 200),
        ("demote", "org2", 403),
    ],
)
async def test_actions_on_organizer_target(
    async_client, register_user, auth_headers, make_member, action, actor, expected
):
    club_id, u = await _setup(async_client, register_user, auth_headers, make_member, f"o{action}{actor}")
    target = u["org3"]["id"]
    h = u[actor]["h"]
    if action == "kick":
        resp = await async_client.delete(_url(club_id, target), headers=h)
    elif action == "ban":
        resp = await async_client.post(_url(club_id, target, "/ban"), headers=h, json={"duration": 1})
    else:
        resp = await async_client.patch(_url(club_id, target, "/role"), headers=h, json={"role": "member"})
    assert resp.status_code == expected
    if expected == 403:
        assert resp.json()["detail"]["code"] == "OWNER_ONLY"
        members = (await async_client.get(f"/api/v1/clubs/{club_id}/members", headers=u["owner"]["h"])).json()
        assert any(m["userId"] == target and m["role"] == "organizer" for m in members)


# organizers still manage ordinary members
@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["owner", "org2"])
async def test_organizers_manage_ordinary_members(async_client, register_user, auth_headers, make_member, actor):
    club_id, u = await _setup(async_client, register_user, auth_headers, make_member, f"mem{actor}")
    h = u[actor]["h"]
    target = u["member"]["id"]
    resp = await async_client.post(_url(club_id, target, "/ban"), headers=h, json={"duration": 1})
    assert resp.status_code == 201
    resp = await async_client.delete(f"/api/v1/clubs/{club_id}/bans/{target}", headers=h)
    assert resp.status_code == 204
    await make_member(club_id, u["member"]["h"])
    resp = await async_client.delete(_url(club_id, target), headers=h)
    assert resp.status_code == 204


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["owner", "org2"])
async def test_organizers_can_promote_members(async_client, register_user, auth_headers, make_member, actor):
    club_id, u = await _setup(async_client, register_user, auth_headers, make_member, f"pro{actor}")
    resp = await async_client.patch(
        _url(club_id, u["member"]["id"], "/role"), headers=u[actor]["h"], json={"role": "organizer"}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_plain_member_cannot_act(async_client, register_user, auth_headers, make_member):
    club_id, u = await _setup(async_client, register_user, auth_headers, make_member, "plain")
    h = u["member"]["h"]
    r = await async_client.delete(_url(club_id, u["org2"]["id"]), headers=h)
    assert r.status_code == 403
    r = await async_client.post(_url(club_id, u["org2"]["id"], "/ban"), headers=h, json={"duration": 1})
    assert r.status_code == 403
    r = await async_client.delete(f"/api/v1/clubs/{club_id}", headers=h)
    assert r.status_code == 403


# owner target is protected from everyone
@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["owner", "org2"])
async def test_owner_target_protected(async_client, register_user, auth_headers, make_member, actor):
    club_id, u = await _setup(async_client, register_user, auth_headers, make_member, f"prot{actor}")
    h, owner = u[actor]["h"], u["owner"]["id"]
    r = await async_client.delete(_url(club_id, owner), headers=h)
    assert (r.status_code, r.json()["detail"]["code"]) == (400, "CANNOT_REMOVE_OWNER")
    r = await async_client.post(_url(club_id, owner, "/ban"), headers=h, json={"duration": 1})
    assert (r.status_code, r.json()["detail"]["code"]) == (400, "CANNOT_REMOVE_OWNER")
    r = await async_client.patch(_url(club_id, owner, "/role"), headers=h, json={"role": "member"})
    assert (r.status_code, r.json()["detail"]["code"]) == (400, "CANNOT_DEMOTE_OWNER")


# delete club: owner only
@pytest.mark.asyncio
async def test_delete_club_owner_only(async_client, register_user, auth_headers, make_member):
    club_id, u = await _setup(async_client, register_user, auth_headers, make_member, "del")
    resp = await async_client.delete(f"/api/v1/clubs/{club_id}", headers=u["org2"]["h"])
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "OWNER_ONLY"
    r = await async_client.get(f"/api/v1/clubs/{club_id}", headers=u["owner"]["h"])
    assert r.status_code == 200
    r = await async_client.delete(f"/api/v1/clubs/{club_id}", headers=u["owner"]["h"])
    assert r.status_code == 204


# self-leave and self-demote for a non-owner organizer stay possible
@pytest.mark.asyncio
async def test_non_owner_organizer_can_step_down_and_leave(async_client, register_user, auth_headers, make_member):
    club_id, u = await _setup(async_client, register_user, auth_headers, make_member, "self")
    h = u["org2"]["h"]
    resp = await async_client.patch(_url(club_id, u["org2"]["id"], "/role"), headers=h, json={"role": "member"})
    assert resp.status_code == 200
    resp = await async_client.delete(f"/api/v1/clubs/{club_id}/leave", headers=u["org3"]["h"])
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_owner_cannot_leave(async_client, register_user, auth_headers, make_member):
    club_id, u = await _setup(async_client, register_user, auth_headers, make_member, "ownleave")
    r = await async_client.delete(f"/api/v1/clubs/{club_id}/leave", headers=u["owner"]["h"])
    assert (r.status_code, r.json()["detail"]["code"]) == (400, "OWNER_CANNOT_LEAVE")


@pytest.mark.asyncio
async def test_self_kick_by_organizer_allowed_and_by_owner_rejected(
    async_client, register_user, auth_headers, make_member
):
    club_id, u = await _setup(async_client, register_user, auth_headers, make_member, "selfkick")
    r = await async_client.delete(_url(club_id, u["org2"]["id"]), headers=u["org2"]["h"])
    assert r.status_code == 204
    r = await async_client.delete(_url(club_id, u["owner"]["id"]), headers=u["owner"]["h"])
    assert (r.status_code, r.json()["detail"]["code"]) == (400, "CANNOT_REMOVE_OWNER")


@pytest.mark.asyncio
async def test_self_ban_rejected(async_client, register_user, auth_headers, make_member):
    club_id, u = await _setup(async_client, register_user, auth_headers, make_member, "selfban")
    r = await async_client.post(_url(club_id, u["org2"]["id"], "/ban"), headers=u["org2"]["h"], json={"duration": 1})
    assert (r.status_code, r.json()["detail"]["code"]) == (400, "CANNOT_BAN_SELF")


@pytest.mark.asyncio
async def test_plain_member_can_still_leave(async_client, register_user, auth_headers, make_member):
    club_id, u = await _setup(async_client, register_user, auth_headers, make_member, "memleave")
    r = await async_client.delete(f"/api/v1/clubs/{club_id}/leave", headers=u["member"]["h"])
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_last_organizer_guard_is_defensive(async_client, register_user, auth_headers, make_member, db_session):
    """The owner always keeps an organizer row, so LAST_ORGANIZER is only reachable if that row is
    missing (data corruption); the guard must still hold for leave and self-demote/kick."""
    import uuid as _uuid

    from sqlalchemy import delete

    from app.models.club_member import ClubMember

    club_id, u = await _setup(async_client, register_user, auth_headers, make_member, "lastorg")
    await db_session.execute(
        delete(ClubMember).where(
            ClubMember.club_id == _uuid.UUID(club_id), ClubMember.user_id == _uuid.UUID(u["owner"]["id"])
        )
    )
    await db_session.execute(
        delete(ClubMember).where(
            ClubMember.club_id == _uuid.UUID(club_id), ClubMember.user_id == _uuid.UUID(u["org3"]["id"])
        )
    )
    await db_session.commit()
    h, me = u["org2"]["h"], u["org2"]["id"]
    r = await async_client.delete(f"/api/v1/clubs/{club_id}/leave", headers=h)
    assert (r.status_code, r.json()["detail"]["code"]) == (400, "LAST_ORGANIZER")
    r = await async_client.patch(_url(club_id, me, "/role"), headers=h, json={"role": "member"})
    assert (r.status_code, r.json()["detail"]["code"]) == (400, "LAST_ORGANIZER")
    r = await async_client.delete(_url(club_id, me), headers=h)
    assert (r.status_code, r.json()["detail"]["code"]) == (400, "LAST_ORGANIZER")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["kick", "ban", "role"])
async def test_actor_demoted_while_waiting_for_lock_is_rejected(
    async_client, register_user, auth_headers, make_member, db_session, monkeypatch, action
):
    """Simulate the race: the actor passes the permission check, is demoted before the lock is granted."""
    import uuid as _uuid

    from sqlalchemy import update

    from app.models.club_member import ClubMember
    from app.services import club_service

    club_id, u = await _setup(async_client, register_user, auth_headers, make_member, f"race{action}")
    original = club_service.lock_club_for_update

    async def demote_then_lock(cid, db):
        await db_session.execute(
            update(ClubMember)
            .where(ClubMember.club_id == _uuid.UUID(club_id), ClubMember.user_id == _uuid.UUID(u["org2"]["id"]))
            .values(role="member")
        )
        await db_session.commit()
        return await original(cid, db)

    monkeypatch.setattr(club_service, "lock_club_for_update", demote_then_lock)
    h, target = u["org2"]["h"], u["member"]["id"]
    if action == "kick":
        r = await async_client.delete(_url(club_id, target), headers=h)
    elif action == "ban":
        r = await async_client.post(_url(club_id, target, "/ban"), headers=h, json={"duration": 1})
    else:
        r = await async_client.patch(_url(club_id, target, "/role"), headers=h, json={"role": "organizer"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "FORBIDDEN"
