import uuid

import pytest


async def create_organizer_with_club(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "Vote Club", "description": "Desc", "city": "Kyiv"}
    )
    return headers, club_resp.json()["id"]


@pytest.mark.asyncio
async def test_get_current_round_null_when_none_created(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/book-vote/round", headers=headers)
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
async def test_get_current_round_requires_membership(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    await register_user(email="outsider@example.com")
    outsider_headers = await auth_headers(email="outsider@example.com")
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/book-vote/round", headers=outsider_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_round_requires_organizer(async_client, register_user, auth_headers, make_member):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    await register_user(email="member@example.com")
    member_headers = await auth_headers(email="member@example.com")
    await make_member(club_id, member_headers)

    resp = await async_client.post(f"/api/v1/clubs/{club_id}/book-vote/rounds", headers=member_headers)
    assert resp.status_code == 403

    resp = await async_client.post(f"/api/v1/clubs/{club_id}/book-vote/rounds", headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "open"
    assert body["options"] == []
    assert body["totalVotes"] == 0


@pytest.mark.asyncio
async def test_add_option_and_vote_flow(async_client, register_user, auth_headers, make_member):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    await register_user(email="member@example.com")
    member_headers = await auth_headers(email="member@example.com")
    await make_member(club_id, member_headers)

    round_resp = await async_client.post(f"/api/v1/clubs/{club_id}/book-vote/rounds", headers=headers)
    round_id = round_resp.json()["id"]

    opt_resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/book-vote/rounds/{round_id}/options",
        headers=headers,
        json={"title": "Dune", "author": "Frank Herbert"},
    )
    assert opt_resp.status_code == 201
    option_id = opt_resp.json()["options"][0]["id"]

    vote_resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/book-vote/options/{option_id}/vote", headers=member_headers
    )
    assert vote_resp.status_code == 200
    option = vote_resp.json()["options"][0]
    assert option["votes"] == 1
    assert option["hasVoted"] is True
    assert vote_resp.json()["totalVotes"] == 1

    unvote_resp = await async_client.request(
        "DELETE", f"/api/v1/clubs/{club_id}/book-vote/options/{option_id}/vote", headers=member_headers
    )
    assert unvote_resp.status_code == 200
    option = unvote_resp.json()["options"][0]
    assert option["votes"] == 0
    assert option["hasVoted"] is False


@pytest.mark.asyncio
async def test_vote_moves_between_options_in_same_round(async_client, register_user, auth_headers, make_member):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    await register_user(email="member@example.com")
    member_headers = await auth_headers(email="member@example.com")
    await make_member(club_id, member_headers)

    round_resp = await async_client.post(f"/api/v1/clubs/{club_id}/book-vote/rounds", headers=headers)
    round_id = round_resp.json()["id"]

    opt1 = (
        await async_client.post(
            f"/api/v1/clubs/{club_id}/book-vote/rounds/{round_id}/options",
            headers=headers,
            json={"title": "Book A", "author": ""},
        )
    ).json()["options"][0]["id"]
    opt2_resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/book-vote/rounds/{round_id}/options",
        headers=headers,
        json={"title": "Book B", "author": ""},
    )
    opt2 = next(o["id"] for o in opt2_resp.json()["options"] if o["title"] == "Book B")

    await async_client.post(f"/api/v1/clubs/{club_id}/book-vote/options/{opt1}/vote", headers=member_headers)
    resp = await async_client.post(f"/api/v1/clubs/{club_id}/book-vote/options/{opt2}/vote", headers=member_headers)

    assert resp.status_code == 200
    by_id = {o["id"]: o for o in resp.json()["options"]}
    assert by_id[opt1]["votes"] == 0
    assert by_id[opt1]["hasVoted"] is False
    assert by_id[opt2]["votes"] == 1
    assert by_id[opt2]["hasVoted"] is True
    assert resp.json()["totalVotes"] == 1


@pytest.mark.asyncio
async def test_remove_option_blocked_when_it_has_votes(async_client, register_user, auth_headers, make_member):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    await register_user(email="member@example.com")
    member_headers = await auth_headers(email="member@example.com")
    await make_member(club_id, member_headers)

    round_resp = await async_client.post(f"/api/v1/clubs/{club_id}/book-vote/rounds", headers=headers)
    round_id = round_resp.json()["id"]
    opt_resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/book-vote/rounds/{round_id}/options",
        headers=headers,
        json={"title": "Dune", "author": ""},
    )
    option_id = opt_resp.json()["options"][0]["id"]
    await async_client.post(f"/api/v1/clubs/{club_id}/book-vote/options/{option_id}/vote", headers=member_headers)

    resp = await async_client.request(
        "DELETE", f"/api/v1/clubs/{club_id}/book-vote/options/{option_id}", headers=headers
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_close_round_picks_winner_and_blocks_further_votes(
    async_client, register_user, auth_headers, make_member
):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    await register_user(email="member@example.com")
    member_headers = await auth_headers(email="member@example.com")
    await make_member(club_id, member_headers)

    round_resp = await async_client.post(f"/api/v1/clubs/{club_id}/book-vote/rounds", headers=headers)
    round_id = round_resp.json()["id"]
    opt_resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/book-vote/rounds/{round_id}/options",
        headers=headers,
        json={"title": "Dune", "author": ""},
    )
    option_id = opt_resp.json()["options"][0]["id"]
    await async_client.post(f"/api/v1/clubs/{club_id}/book-vote/options/{option_id}/vote", headers=member_headers)

    close_resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/book-vote/rounds/{round_id}/close", headers=headers
    )
    assert close_resp.status_code == 200
    body = close_resp.json()
    assert body["status"] == "closed"
    assert body["winnerId"] == option_id

    late_vote = await async_client.post(
        f"/api/v1/clubs/{club_id}/book-vote/options/{option_id}/vote", headers=member_headers
    )
    assert late_vote.status_code == 409


@pytest.mark.asyncio
async def test_option_not_found(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/book-vote/options/{uuid.uuid4()}/vote", headers=headers
    )
    assert resp.status_code == 404
