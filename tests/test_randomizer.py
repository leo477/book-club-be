import uuid

import pytest

from app.models.randomizer import RandomizerSession


async def create_organizer_with_club(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "Randomizer Club", "description": "Desc", "city": "Kyiv"}
    )
    club_id = club_resp.json()["id"]
    return headers, club_id


@pytest.mark.asyncio
async def test_randomizer_history_empty(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/randomizer/history", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def _me(async_client, headers):
    return (await async_client.get("/api/v1/users/me", headers=headers)).json()


async def _add_member(async_client, auth_headers, make_member, club_id, email, name):
    headers = await auth_headers(email=email, displayName=name)
    await make_member(club_id, headers)
    return await _me(async_client, headers)


def _url(club_id):
    return f"/api/v1/clubs/{club_id}/randomizer/sessions"


@pytest.mark.asyncio
async def test_create_randomizer_session(async_client, register_user, auth_headers, make_member):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    me = await _me(async_client, headers)
    bob = await _add_member(async_client, auth_headers, make_member, club_id, "bob@example.com", "Bob")
    payload = {
        "purpose": "Pick a winner",
        "candidates": [
            {"userId": me["id"], "displayName": "forged", "avatarUrl": "http://evil"},
            {"userId": bob["id"], "displayName": "x"},
        ],
        "result": {"userId": bob["id"], "displayName": "x"},
    }
    resp = await async_client.post(_url(club_id), headers=headers, json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["purpose"] == "Pick a winner"
    assert data["candidates"][0]["displayName"] == me["displayName"]
    assert data["candidates"][0]["avatarUrl"] == me.get("avatarUrl")
    assert data["result"]["displayName"] == "Bob"


@pytest.mark.asyncio
async def test_randomizer_history_after_create(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    me = await _me(async_client, headers)
    payload = {"purpose": "Pick a winner", "candidates": [{"userId": me["id"], "displayName": "A"}], "result": None}
    await async_client.post(_url(club_id), headers=headers, json=payload)
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/randomizer/history", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["candidates"][0]["userId"] == me["id"]


@pytest.mark.asyncio
async def test_randomizer_purpose_too_long(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    resp = await async_client.post(_url(club_id), headers=headers, json={"purpose": "x" * 201, "candidates": []})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_randomizer_too_many_candidates(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    candidates = [{"userId": str(uuid.uuid4()), "displayName": "A"} for _ in range(201)]
    resp = await async_client.post(_url(club_id), headers=headers, json={"purpose": "p", "candidates": candidates})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_randomizer_non_member_candidate(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    outsider = await _me(async_client, await auth_headers(email="out@example.com"))
    payload = {"purpose": "p", "candidates": [{"userId": outsider["id"], "displayName": "O"}]}
    resp = await async_client.post(_url(club_id), headers=headers, json=payload)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "CANDIDATE_NOT_MEMBER"


@pytest.mark.asyncio
async def test_randomizer_invalid_candidate_id(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    payload = {"purpose": "p", "candidates": [{"userId": "u1", "displayName": "A"}]}
    resp = await async_client.post(_url(club_id), headers=headers, json=payload)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_CANDIDATE"


@pytest.mark.asyncio
async def test_randomizer_result_not_among_candidates(async_client, register_user, auth_headers, make_member):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    me = await _me(async_client, headers)
    bob = await _add_member(async_client, auth_headers, make_member, club_id, "bob@example.com", "Bob")
    payload = {
        "purpose": "p",
        "candidates": [{"userId": me["id"], "displayName": "A"}],
        "result": {"userId": bob["id"], "displayName": "Bob"},
    }
    resp = await async_client.post(_url(club_id), headers=headers, json=payload)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "RESULT_NOT_CANDIDATE"


@pytest.mark.asyncio
async def test_randomizer_duplicate_candidates(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    me = await _me(async_client, headers)
    c = {"userId": me["id"], "displayName": "A"}
    resp = await async_client.post(_url(club_id), headers=headers, json={"purpose": "p", "candidates": [c, c]})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "DUPLICATE_CANDIDATE"


@pytest.mark.asyncio
async def test_randomizer_history_non_member_forbidden(async_client, register_user, auth_headers):
    """Bug 1: a non-member must not be able to read another club's randomizer history."""
    _headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)

    await register_user(email="outsider@example.com")
    outsider_headers = await auth_headers(email="outsider@example.com")

    resp = await async_client.get(f"/api/v1/clubs/{club_id}/randomizer/history", headers=outsider_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_randomizer_session_non_member_forbidden(async_client, register_user, auth_headers):
    """Bug 1: a non-member must not be able to create a randomizer session for a club."""
    _headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)

    await register_user(email="outsider2@example.com")
    outsider_headers = await auth_headers(email="outsider2@example.com")

    candidates = [{"userId": str(uuid.uuid4()), "displayName": "Alice", "avatarUrl": None}]
    payload = {"purpose": "Pick a winner", "candidates": candidates, "result": None}
    resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/randomizer/sessions", headers=outsider_headers, json=payload
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_randomizer_duplicate_candidates_different_format(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    me = await _me(async_client, headers)
    candidates = [
        {"userId": me["id"], "displayName": "A"},
        {"userId": me["id"].upper().replace("-", ""), "displayName": "A"},
    ]
    resp = await async_client.post(_url(club_id), headers=headers, json={"purpose": "p", "candidates": candidates})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "DUPLICATE_CANDIDATE"


@pytest.mark.asyncio
async def test_randomizer_empty_candidates_without_result(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    resp = await async_client.post(
        _url(club_id), headers=headers, json={"purpose": "p", "candidates": [], "result": None}
    )
    assert resp.status_code == 201
    assert resp.json()["candidates"] == []
    assert resp.json()["result"] is None


@pytest.mark.asyncio
async def test_randomizer_empty_candidates_with_result(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    me = await _me(async_client, headers)
    payload = {"purpose": "p", "candidates": [], "result": {"userId": me["id"], "displayName": "A"}}
    resp = await async_client.post(_url(club_id), headers=headers, json=payload)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "RESULT_NOT_CANDIDATE"


@pytest.mark.asyncio
async def test_randomizer_result_not_a_uuid(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    me = await _me(async_client, headers)
    payload = {
        "purpose": "p",
        "candidates": [{"userId": me["id"], "displayName": "A"}],
        "result": {"userId": "nope", "displayName": "A"},
    }
    resp = await async_client.post(_url(club_id), headers=headers, json=payload)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "RESULT_NOT_CANDIDATE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidate",
    [
        {"userId": "x" * 37, "displayName": "A"},
        {"userId": str(uuid.uuid4()), "displayName": "x" * 101},
        {"userId": str(uuid.uuid4()), "displayName": "A", "avatarUrl": "x" * 2049},
    ],
)
async def test_randomizer_candidate_field_too_long(async_client, register_user, auth_headers, candidate):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    resp = await async_client.post(_url(club_id), headers=headers, json={"purpose": "p", "candidates": [candidate]})
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["remove", "ban"])
async def test_randomizer_former_member_rejected(async_client, register_user, auth_headers, make_member, how):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    bob = await _add_member(async_client, auth_headers, make_member, club_id, "bob@example.com", "Bob")
    base = f"/api/v1/clubs/{club_id}"
    if how == "remove":
        gone = await async_client.delete(f"{base}/members/{bob['id']}", headers=headers)
        assert gone.status_code == 204
    else:
        gone = await async_client.post(
            f"{base}/members/{bob['id']}/ban", headers=headers, json={"duration": "permanent"}
        )
        assert gone.status_code == 201
    payload = {"purpose": "p", "candidates": [{"userId": bob["id"], "displayName": "Bob"}]}
    resp = await async_client.post(_url(club_id), headers=headers, json=payload)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "CANDIDATE_NOT_MEMBER"


@pytest.mark.asyncio
async def test_randomizer_forged_avatar_not_stored(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    me = await _me(async_client, headers)
    candidate = {"userId": me["id"], "displayName": "forged", "avatarUrl": "http://evil"}
    payload = {"purpose": "p", "candidates": [candidate], "result": candidate}
    resp = await async_client.post(_url(club_id), headers=headers, json=payload)
    assert resp.status_code == 201
    assert "http://evil" not in resp.text
    history = await async_client.get(f"/api/v1/clubs/{club_id}/randomizer/history", headers=headers)
    assert "http://evil" not in history.text


@pytest.mark.asyncio
async def test_randomizer_history_serves_legacy_oversized_rows(async_client, register_user, auth_headers, db_session):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    me = await _me(async_client, headers)
    legacy = {"userId": "u" * 50, "displayName": "n" * 150, "avatarUrl": "http://x/" + "a" * 2100}
    db_session.add(
        RandomizerSession(
            club_id=uuid.UUID(club_id),
            created_by=uuid.UUID(me["id"]),
            purpose="old",
            candidates=[legacy],
            result=legacy,
        )
    )
    await db_session.commit()
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/randomizer/history", headers=headers)
    assert resp.status_code == 200
    assert resp.json()[0]["candidates"][0]["displayName"] == "n" * 150
