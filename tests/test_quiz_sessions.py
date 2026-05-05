import uuid

import pytest


async def create_organizer_with_quiz(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "Session Club", "description": "Desc", "city": "Kyiv"}
    )
    club_id = club_resp.json()["id"]
    quiz_resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Session Quiz"}
    )
    quiz_id = quiz_resp.json()["id"]
    return headers, club_id, quiz_id


# --- GET /quizzes/{quiz_id} ---


@pytest.mark.asyncio
async def test_get_quiz(async_client, register_user, auth_headers):
    headers, _club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    resp = await async_client.get(f"/api/v1/quizzes/{quiz_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == quiz_id


@pytest.mark.asyncio
async def test_get_quiz_not_found(async_client, register_user, auth_headers):
    await register_user(email="gq_user@example.com")
    headers = await auth_headers(email="gq_user@example.com")
    resp = await async_client.get(f"/api/v1/quizzes/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


# --- PATCH /quizzes/{quiz_id} ---


@pytest.mark.asyncio
async def test_update_quiz(async_client, register_user, auth_headers):
    headers, _club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    resp = await async_client.patch(
        f"/api/v1/quizzes/{quiz_id}", headers=headers, json={"title": "Updated Title", "description": "New desc"}
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Updated Title"


@pytest.mark.asyncio
async def test_update_quiz_not_organizer(async_client, register_user, auth_headers):
    headers, club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    await register_user(email="uq_member@example.com")
    headers2 = await auth_headers(email="uq_member@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.patch(
        f"/api/v1/quizzes/{quiz_id}", headers=headers2, json={"title": "Hack", "description": ""}
    )
    assert resp.status_code == 403


# --- PATCH /quizzes/{quiz_id}/questions/{question_id} ---


@pytest.mark.asyncio
async def test_update_question(async_client, register_user, auth_headers):
    headers, _club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    q_resp = await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/questions",
        headers=headers,
        json={"question": "Original?", "options": ["A", "B"], "correctIndex": 0},
    )
    question_id = q_resp.json()["id"]
    resp = await async_client.patch(
        f"/api/v1/quizzes/{quiz_id}/questions/{question_id}",
        headers=headers,
        json={"question": "Updated?", "options": ["X", "Y"], "correctIndex": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["question"] == "Updated?"
    assert resp.json()["correctIndex"] == 1


@pytest.mark.asyncio
async def test_update_question_not_found(async_client, register_user, auth_headers):
    headers, _club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    resp = await async_client.patch(
        f"/api/v1/quizzes/{quiz_id}/questions/{uuid.uuid4()}",
        headers=headers,
        json={"question": "X?"},
    )
    assert resp.status_code == 404


# --- DELETE /quizzes/{quiz_id}/questions/{question_id} ---


@pytest.mark.asyncio
async def test_delete_question(async_client, register_user, auth_headers):
    headers, _club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    q_resp = await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/questions",
        headers=headers,
        json={"question": "Delete me?", "options": ["A", "B"], "correctIndex": 0},
    )
    question_id = q_resp.json()["id"]
    resp = await async_client.delete(f"/api/v1/quizzes/{quiz_id}/questions/{question_id}", headers=headers)
    assert resp.status_code == 204
    # Verify it's gone
    list_resp = await async_client.get(f"/api/v1/quizzes/{quiz_id}/questions", headers=headers)
    assert all(q["id"] != question_id for q in list_resp.json())


@pytest.mark.asyncio
async def test_delete_question_not_found(async_client, register_user, auth_headers):
    headers, _club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    resp = await async_client.delete(f"/api/v1/quizzes/{quiz_id}/questions/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


# --- PUT /quizzes/{quiz_id}/questions/order ---


@pytest.mark.asyncio
async def test_reorder_questions(async_client, register_user, auth_headers):
    headers, _club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    q1 = (
        await async_client.post(
            f"/api/v1/quizzes/{quiz_id}/questions",
            headers=headers,
            json={"question": "Q1", "options": ["A", "B"], "correctIndex": 0},
        )
    ).json()["id"]
    q2 = (
        await async_client.post(
            f"/api/v1/quizzes/{quiz_id}/questions",
            headers=headers,
            json={"question": "Q2", "options": ["A", "B"], "correctIndex": 1},
        )
    ).json()["id"]
    resp = await async_client.put(
        f"/api/v1/quizzes/{quiz_id}/questions/order",
        headers=headers,
        json={"order": [q2, q1]},
    )
    assert resp.status_code == 204
    questions = (await async_client.get(f"/api/v1/quizzes/{quiz_id}/questions", headers=headers)).json()
    assert questions[0]["id"] == q2
    assert questions[1]["id"] == q1


# --- POST /quizzes/{quiz_id}/sessions ---


@pytest.mark.asyncio
async def test_create_session(async_client, register_user, auth_headers):
    headers, _club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    resp = await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/sessions",
        headers=headers,
        json={"eventId": str(uuid.uuid4())},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert data["quizId"] == quiz_id
    assert data["closedAt"] is None


@pytest.mark.asyncio
async def test_create_session_not_organizer(async_client, register_user, auth_headers):
    headers, club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    await register_user(email="cs_member@example.com")
    headers2 = await auth_headers(email="cs_member@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/sessions",
        headers=headers2,
        json={"eventId": str(uuid.uuid4())},
    )
    assert resp.status_code == 403


# --- GET /quizzes/{quiz_id}/sessions/active ---


@pytest.mark.asyncio
async def test_get_active_session(async_client, register_user, auth_headers):
    headers, _club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/sessions",
        headers=headers,
        json={"eventId": str(uuid.uuid4())},
    )
    resp = await async_client.get(f"/api/v1/quizzes/{quiz_id}/sessions/active", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["quizId"] == quiz_id


@pytest.mark.asyncio
async def test_get_active_session_not_found(async_client, register_user, auth_headers):
    headers, _club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    resp = await async_client.get(f"/api/v1/quizzes/{quiz_id}/sessions/active", headers=headers)
    assert resp.status_code == 404


# --- GET /quizzes/{quiz_id}/sessions/{session_id}/leaderboard ---


@pytest.mark.asyncio
async def test_get_leaderboard(async_client, register_user, auth_headers):
    headers, club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    # Add a question and activate
    await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/questions",
        headers=headers,
        json={"question": "Q?", "options": ["A", "B"], "correctIndex": 0},
    )
    await async_client.patch(f"/api/v1/quizzes/{quiz_id}/active", headers=headers, json={"isActive": True})
    session_resp = await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/sessions",
        headers=headers,
        json={"eventId": str(uuid.uuid4())},
    )
    session_id = session_resp.json()["id"]
    # Submit an attempt as member
    await register_user(email="lb_member@example.com")
    headers2 = await auth_headers(email="lb_member@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    await async_client.post(f"/api/v1/quizzes/{quiz_id}/attempts", headers=headers2, json={"answers": [0]})

    resp = await async_client.get(f"/api/v1/quizzes/{quiz_id}/sessions/{session_id}/leaderboard", headers=headers)
    assert resp.status_code == 200
    assert "entries" in resp.json()


@pytest.mark.asyncio
async def test_get_leaderboard_session_not_found(async_client, register_user, auth_headers):
    headers, _club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    resp = await async_client.get(f"/api/v1/quizzes/{quiz_id}/sessions/{uuid.uuid4()}/leaderboard", headers=headers)
    assert resp.status_code == 404


# --- PATCH /quizzes/{quiz_id}/sessions/{session_id}/close ---


@pytest.mark.asyncio
async def test_close_session(async_client, register_user, auth_headers):
    headers, _club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    session_resp = await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/sessions",
        headers=headers,
        json={"eventId": str(uuid.uuid4())},
    )
    session_id = session_resp.json()["id"]
    resp = await async_client.patch(f"/api/v1/quizzes/{quiz_id}/sessions/{session_id}/close", headers=headers)
    assert resp.status_code == 204
    # Active session should now be gone
    active_resp = await async_client.get(f"/api/v1/quizzes/{quiz_id}/sessions/active", headers=headers)
    assert active_resp.status_code == 404


@pytest.mark.asyncio
async def test_close_session_not_found(async_client, register_user, auth_headers):
    headers, _club_id, quiz_id = await create_organizer_with_quiz(async_client, register_user, auth_headers)
    resp = await async_client.patch(f"/api/v1/quizzes/{quiz_id}/sessions/{uuid.uuid4()}/close", headers=headers)
    assert resp.status_code == 404
