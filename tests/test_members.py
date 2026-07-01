import uuid

import pytest


async def create_organizer_with_club(async_client, register_user, auth_headers, email, club_name):
    await register_user(email=email, role="user")
    headers = await auth_headers(email=email)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": club_name, "description": "Desc", "city": "Kyiv"}
    )
    return headers, club_resp.json()["id"]


@pytest.mark.asyncio
async def test_list_members_organizer_only(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg1@example.com", club_name="MClub1"
    )
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/members", headers=headers)
    assert resp.status_code == 200
    members = resp.json()
    assert isinstance(members, list)
    assert len(members) == 1
    assert members[0]["role"] == "organizer"


@pytest.mark.asyncio
async def test_list_members_with_joined_user(async_client, register_user, auth_headers, make_member):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg2@example.com", club_name="MClub2"
    )
    await register_user(email="muser1@example.com")
    user_headers = await auth_headers(email="muser1@example.com")
    await make_member(club_id, user_headers)

    resp = await async_client.get(f"/api/v1/clubs/{club_id}/members", headers=headers)
    assert resp.status_code == 200
    members = resp.json()
    assert len(members) == 2
    roles = {m["role"] for m in members}
    assert "member" in roles


@pytest.mark.asyncio
async def test_list_members_with_socials_public(async_client, register_user, auth_headers, make_member):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg3@example.com", club_name="MClub3"
    )
    await register_user(email="muser2@example.com")
    user_headers = await auth_headers(email="muser2@example.com")
    await async_client.patch("/api/v1/users/me/socials", headers=user_headers, json={"telegram": "@tguser"})
    await async_client.patch("/api/v1/users/me/socials-visibility", headers=user_headers, json={"socialsPublic": True})
    await make_member(club_id, user_headers)

    resp = await async_client.get(f"/api/v1/clubs/{club_id}/members", headers=headers)
    assert resp.status_code == 200
    members = resp.json()
    public_member = next((m for m in members if m["socialsPublic"] is True), None)
    assert public_member is not None
    assert public_member["socials"] is not None
    assert public_member["socials"].get("telegram") == "@tguser"


@pytest.mark.asyncio
async def test_remove_member_success(async_client, register_user, auth_headers, make_member):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg4@example.com", club_name="MClub4"
    )
    await register_user(email="muser3@example.com")
    user_headers = await auth_headers(email="muser3@example.com")
    await make_member(club_id, user_headers)

    members_resp = await async_client.get(f"/api/v1/clubs/{club_id}/members", headers=headers)
    members = members_resp.json()
    member = next(m for m in members if m["role"] == "member")
    user_id = member["userId"]

    resp = await async_client.delete(f"/api/v1/clubs/{club_id}/members/{user_id}", headers=headers)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_remove_member_not_organizer(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg5@example.com", club_name="MClub5"
    )
    await register_user(email="muser4@example.com")
    user_headers = await auth_headers(email="muser4@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)

    fake_user_id = str(uuid.uuid4())
    resp = await async_client.delete(f"/api/v1/clubs/{club_id}/members/{fake_user_id}", headers=user_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_remove_member_not_found(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg6@example.com", club_name="MClub6"
    )
    fake_user_id = str(uuid.uuid4())
    resp = await async_client.delete(f"/api/v1/clubs/{club_id}/members/{fake_user_id}", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ban_member_success(async_client, register_user, auth_headers, make_member):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg7@example.com", club_name="MClub7"
    )
    await register_user(email="muser5@example.com")
    user_headers = await auth_headers(email="muser5@example.com")
    await make_member(club_id, user_headers)

    members_resp = await async_client.get(f"/api/v1/clubs/{club_id}/members", headers=headers)
    members = members_resp.json()
    member = next(m for m in members if m["role"] == "member")
    user_id = member["userId"]

    resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/members/{user_id}/ban",
        headers=headers,
        json={"duration": 1},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["userId"] == user_id
    assert data["clubId"] == club_id


@pytest.mark.asyncio
async def test_ban_member_not_organizer(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg8@example.com", club_name="MClub8"
    )
    await register_user(email="muser6@example.com")
    user_headers = await auth_headers(email="muser6@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)

    fake_user_id = str(uuid.uuid4())
    resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/members/{fake_user_id}/ban",
        headers=user_headers,
        json={"duration": 1},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ban_member_user_not_found(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg9@example.com", club_name="MClub9"
    )
    fake_user_id = str(uuid.uuid4())
    resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/members/{fake_user_id}/ban",
        headers=headers,
        json={"duration": 1},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ban_member_permanent(async_client, register_user, auth_headers, make_member):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg10@example.com", club_name="MClub10"
    )
    await register_user(email="muser7@example.com")
    user_headers = await auth_headers(email="muser7@example.com")
    await make_member(club_id, user_headers)

    members_resp = await async_client.get(f"/api/v1/clubs/{club_id}/members", headers=headers)
    members = members_resp.json()
    member = next(m for m in members if m["role"] == "member")
    user_id = member["userId"]

    resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/members/{user_id}/ban",
        headers=headers,
        json={"duration": "permanent"},
    )
    assert resp.status_code == 201
    assert resp.json()["duration"] == "permanent"


@pytest.mark.asyncio
async def test_list_bans_empty(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg11@example.com", club_name="MClub11"
    )
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/bans", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_bans_with_bans(async_client, register_user, auth_headers, make_member):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg12@example.com", club_name="MClub12"
    )
    await register_user(email="muser8@example.com")
    user_headers = await auth_headers(email="muser8@example.com")
    await make_member(club_id, user_headers)

    members_resp = await async_client.get(f"/api/v1/clubs/{club_id}/members", headers=headers)
    members = members_resp.json()
    member = next(m for m in members if m["role"] == "member")
    user_id = member["userId"]

    await async_client.post(
        f"/api/v1/clubs/{club_id}/members/{user_id}/ban",
        headers=headers,
        json={"duration": 3},
    )

    resp = await async_client.get(f"/api/v1/clubs/{club_id}/bans", headers=headers)
    assert resp.status_code == 200
    bans = resp.json()
    assert len(bans) >= 1
    assert bans[0]["userId"] == user_id


@pytest.mark.asyncio
async def test_list_bans_not_organizer(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg13@example.com", club_name="MClub13"
    )
    await register_user(email="muser9@example.com")
    user_headers = await auth_headers(email="muser9@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=user_headers)

    resp = await async_client.get(f"/api/v1/clubs/{club_id}/bans", headers=user_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_bans_organizer_via_club_organizer_id(async_client, register_user, auth_headers, db_session):
    """L6: organizer access is granted via Club.organizer_id even if club_members record is absent."""
    import uuid as _uuid

    from sqlalchemy import delete

    from app.models.club_member import ClubMember as ClubMemberModel

    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg14@example.com", club_name="MClub14"
    )

    # Simulate the L6 edge case: remove the organizer's club_members record so
    # require_club_organizer would raise 403, but Club.organizer_id still matches.
    await db_session.execute(delete(ClubMemberModel).where(ClubMemberModel.club_id == _uuid.UUID(club_id)))
    await db_session.commit()

    # Despite missing club_members record, Club.organizer_id fallback allows access.
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/bans", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def _ban_a_member(async_client, register_user, auth_headers, make_member, org_email, club_name, member_email):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email=org_email, club_name=club_name
    )
    await register_user(email=member_email)
    user_headers = await auth_headers(email=member_email)
    user_id = await make_member(club_id, user_headers)
    await async_client.post(f"/api/v1/clubs/{club_id}/members/{user_id}/ban", headers=headers, json={"duration": 1})
    return headers, user_headers, club_id, user_id


@pytest.mark.asyncio
async def test_unban_member_success(async_client, register_user, auth_headers, make_member):
    headers, _user_headers, club_id, user_id = await _ban_a_member(
        async_client, register_user, auth_headers, make_member, "morg15@example.com", "MClub15", "muser10@example.com"
    )
    resp = await async_client.delete(f"/api/v1/clubs/{club_id}/bans/{user_id}", headers=headers)
    assert resp.status_code == 204

    bans_resp = await async_client.get(f"/api/v1/clubs/{club_id}/bans", headers=headers)
    assert bans_resp.json() == []


@pytest.mark.asyncio
async def test_unban_member_not_organizer(async_client, register_user, auth_headers, make_member):
    headers, user_headers, club_id, user_id = await _ban_a_member(
        async_client, register_user, auth_headers, make_member, "morg16@example.com", "MClub16", "muser11@example.com"
    )
    resp = await async_client.delete(f"/api/v1/clubs/{club_id}/bans/{user_id}", headers=user_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unban_nonexistent(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg17@example.com", club_name="MClub17"
    )
    fake_user_id = str(uuid.uuid4())
    resp = await async_client.delete(f"/api/v1/clubs/{club_id}/bans/{fake_user_id}", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_change_member_role_promote(async_client, register_user, auth_headers, make_member):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg18@example.com", club_name="MClub18"
    )
    await register_user(email="muser12@example.com")
    user_headers = await auth_headers(email="muser12@example.com")
    user_id = await make_member(club_id, user_headers)

    resp = await async_client.patch(
        f"/api/v1/clubs/{club_id}/members/{user_id}/role", headers=headers, json={"role": "organizer"}
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "organizer"


@pytest.mark.asyncio
async def test_change_member_role_not_organizer(async_client, register_user, auth_headers, make_member):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg19@example.com", club_name="MClub19"
    )
    await register_user(email="muser13@example.com")
    user_headers = await auth_headers(email="muser13@example.com")
    user_id = await make_member(club_id, user_headers)

    resp = await async_client.patch(
        f"/api/v1/clubs/{club_id}/members/{user_id}/role", headers=user_headers, json={"role": "organizer"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_change_member_role_demote_owner_blocked(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg20@example.com", club_name="MClub20"
    )
    me = await async_client.get("/api/v1/users/me", headers=headers)
    owner_id = me.json()["id"]

    resp = await async_client.patch(
        f"/api/v1/clubs/{club_id}/members/{owner_id}/role", headers=headers, json={"role": "member"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_change_member_role_last_organizer_blocked(
    async_client, register_user, auth_headers, make_member, db_session
):
    import uuid as _uuid

    from sqlalchemy import delete

    from app.models.club_member import ClubMember as ClubMemberModel

    headers, club_id = await create_organizer_with_club(
        async_client, register_user, auth_headers, email="morg21@example.com", club_name="MClub21"
    )
    # Create a second, non-owner organizer.
    await register_user(email="muser14@example.com")
    user_headers = await auth_headers(email="muser14@example.com")
    user_id = await make_member(club_id, user_headers, role="organizer")

    # Remove the owner's membership so the second organizer is the sole organizer.
    me = await async_client.get("/api/v1/users/me", headers=headers)
    owner_id = me.json()["id"]
    await db_session.execute(
        delete(ClubMemberModel).where(
            ClubMemberModel.club_id == _uuid.UUID(club_id),
            ClubMemberModel.user_id == _uuid.UUID(owner_id),
        )
    )
    await db_session.commit()

    # The sole organizer demoting themselves must be blocked.
    resp = await async_client.patch(
        f"/api/v1/clubs/{club_id}/members/{user_id}/role", headers=user_headers, json={"role": "member"}
    )
    assert resp.status_code == 400
