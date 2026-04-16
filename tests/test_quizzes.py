import uuid

import pytest


async def create_organizer_with_club(async_client, register_user, auth_headers):
    await register_user()
    headers = await auth_headers()
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post(
        "/api/v1/clubs", headers=headers, json={"name": "Quiz Club", "description": "Desc", "city": "Kyiv"}
    )
    club_id = club_resp.json()["id"]
    return headers, club_id


@pytest.mark.asyncio
async def test_list_quizzes_empty(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/quizzes", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_quiz_as_organizer(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz 1"})
    assert resp.status_code == 201
    assert "id" in resp.json()


@pytest.mark.asyncio
async def test_create_quiz_not_organizer(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    # Register/join as member
    await register_user(email="user2@example.com")
    headers2 = await auth_headers(email="user2@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers2, json={"title": "Quiz 2"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_add_question(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    quiz_resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz 1"})
    quiz_id = quiz_resp.json()["id"]
    q_resp = await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/questions",
        headers=headers,
        json={"question": "Q1", "options": ["A", "B"], "correctIndex": 0},
    )
    assert q_resp.status_code == 201
    assert q_resp.json()["question"] == "Q1"


@pytest.mark.asyncio
async def test_get_questions_as_organizer(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    quiz_resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz 1"})
    quiz_id = quiz_resp.json()["id"]
    await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/questions",
        headers=headers,
        json={"question": "Q1", "options": ["A", "B"], "correctIndex": 0},
    )
    resp = await async_client.get(f"/api/v1/quizzes/{quiz_id}/questions", headers=headers)
    assert resp.status_code == 200
    assert resp.json()[0]["correctIndex"] == 0


@pytest.mark.asyncio
async def test_get_questions_as_member(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    quiz_resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz 1"})
    quiz_id = quiz_resp.json()["id"]
    await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/questions",
        headers=headers,
        json={"question": "Q1", "options": ["A", "B"], "correctIndex": 0},
    )
    await register_user(email="user2@example.com")
    headers2 = await auth_headers(email="user2@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.get(f"/api/v1/quizzes/{quiz_id}/questions", headers=headers2)
    assert resp.status_code == 200
    assert "correctIndex" not in resp.json()[0] or resp.json()[0]["correctIndex"] is None


@pytest.mark.asyncio
async def test_activate_quiz(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    quiz_resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz 1"})
    quiz_id = quiz_resp.json()["id"]
    resp = await async_client.patch(f"/api/v1/quizzes/{quiz_id}/active", headers=headers, json={"isActive": True})
    assert resp.status_code == 200
    assert resp.json()["isActive"] is True


@pytest.mark.asyncio
async def test_submit_attempt(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    quiz_resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz 1"})
    quiz_id = quiz_resp.json()["id"]
    await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/questions",
        headers=headers,
        json={"question": "Q1", "options": ["A", "B"], "correctIndex": 0},
    )
    await async_client.patch(f"/api/v1/quizzes/{quiz_id}/active", headers=headers, json={"isActive": True})
    await register_user(email="user2@example.com")
    headers2 = await auth_headers(email="user2@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    attempt = {"answers": [0]}
    resp = await async_client.post(f"/api/v1/quizzes/{quiz_id}/attempts", headers=headers2, json=attempt)
    assert resp.status_code == 201
    assert "score" in resp.json()


@pytest.mark.asyncio
async def test_submit_attempt_inactive_quiz(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    quiz_resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz 1"})
    quiz_id = quiz_resp.json()["id"]
    await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/questions",
        headers=headers,
        json={"question": "Q1", "options": ["A", "B"], "correctIndex": 0},
    )
    await register_user(email="user2@example.com")
    headers2 = await auth_headers(email="user2@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    attempt = {"answers": [0]}
    resp = await async_client.post(f"/api/v1/quizzes/{quiz_id}/attempts", headers=headers2, json=attempt)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_questions_quiz_not_found(async_client, register_user, auth_headers):
    await register_user(email="quiz2_user1@example.com")
    headers = await auth_headers(email="quiz2_user1@example.com")
    fake_quiz_id = str(uuid.uuid4())
    resp = await async_client.get(f"/api/v1/quizzes/{fake_quiz_id}/questions", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_question_quiz_not_found(async_client, register_user, auth_headers):
    await register_user(email="quiz2_user2@example.com")
    headers = await auth_headers(email="quiz2_user2@example.com")
    fake_quiz_id = str(uuid.uuid4())
    resp = await async_client.post(
        f"/api/v1/quizzes/{fake_quiz_id}/questions",
        headers=headers,
        json={"question": "Q?", "options": ["A", "B"], "correctIndex": 0},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_question_not_organizer(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    quiz_resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz NQ"})
    quiz_id = quiz_resp.json()["id"]
    await register_user(email="quiz2_user3@example.com")
    headers2 = await auth_headers(email="quiz2_user3@example.com")
    resp = await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/questions",
        headers=headers2,
        json={"question": "Q?", "options": ["A", "B"], "correctIndex": 0},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_set_active_quiz_not_found(async_client, register_user, auth_headers):
    await register_user(email="quiz2_user4@example.com")
    headers = await auth_headers(email="quiz2_user4@example.com")
    fake_quiz_id = str(uuid.uuid4())
    resp = await async_client.patch(
        f"/api/v1/quizzes/{fake_quiz_id}/active",
        headers=headers,
        json={"isActive": True},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_set_active_not_organizer(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    quiz_resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz SA"})
    quiz_id = quiz_resp.json()["id"]
    await register_user(email="quiz2_user5@example.com")
    headers2 = await auth_headers(email="quiz2_user5@example.com")
    resp = await async_client.patch(
        f"/api/v1/quizzes/{quiz_id}/active",
        headers=headers2,
        json={"isActive": True},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_submit_attempt_quiz_not_found(async_client, register_user, auth_headers):
    await register_user(email="quiz2_user6@example.com")
    headers = await auth_headers(email="quiz2_user6@example.com")
    fake_quiz_id = str(uuid.uuid4())
    resp = await async_client.post(
        f"/api/v1/quizzes/{fake_quiz_id}/attempts",
        headers=headers,
        json={"answers": [0]},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_submit_attempt_score_calculation(async_client, register_user, auth_headers):
    headers, club_id = await create_organizer_with_club(async_client, register_user, auth_headers)
    quiz_resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Score Quiz"}
    )
    quiz_id = quiz_resp.json()["id"]
    await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/questions",
        headers=headers,
        json={"question": "Q1", "options": ["A", "B"], "correctIndex": 0},
    )
    await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/questions",
        headers=headers,
        json={"question": "Q2", "options": ["A", "B"], "correctIndex": 1},
    )
    await async_client.patch(f"/api/v1/quizzes/{quiz_id}/active", headers=headers, json={"isActive": True})
    # answers[0]=0 correct, answers[1]=0 wrong (correct is 1)
    resp = await async_client.post(
        f"/api/v1/quizzes/{quiz_id}/attempts",
        headers=headers,
        json={"answers": [0, 0]},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["score"] == 1
    assert data["total"] == 2
