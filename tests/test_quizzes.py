import pytest


# Helpers
async def register_user(async_client, email="test@example.com", password="password123", displayName="Test User", role="user"):
    return await async_client.post("/api/v1/auth/register", json={
        "email": email, "password": password, "displayName": displayName, "role": role
    })

async def get_auth_headers(async_client, email="test@example.com", password="password123"):
    resp = await async_client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}

async def create_organizer_with_club(async_client):
    await register_user(async_client)
    headers = await get_auth_headers(async_client)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    club_resp = await async_client.post("/api/v1/clubs", headers=headers, json={"name": "Quiz Club", "description": "Desc", "city": "Kyiv"})
    club_id = club_resp.json()["id"]
    return headers, club_id

@pytest.mark.asyncio
async def test_list_quizzes_empty(async_client):
    headers, club_id = await create_organizer_with_club(async_client)
    resp = await async_client.get(f"/api/v1/clubs/{club_id}/quizzes", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []

@pytest.mark.asyncio
async def test_create_quiz_as_organizer(async_client):
    headers, club_id = await create_organizer_with_club(async_client)
    resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz 1"})
    assert resp.status_code == 201
    assert "id" in resp.json()

@pytest.mark.asyncio
async def test_create_quiz_not_organizer(async_client):
    headers, club_id = await create_organizer_with_club(async_client)
    # Register/join as member
    await register_user(async_client, email="user2@example.com")
    headers2 = await get_auth_headers(async_client, email="user2@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers2, json={"title": "Quiz 2"})
    assert resp.status_code == 403

@pytest.mark.asyncio
async def test_add_question(async_client):
    headers, club_id = await create_organizer_with_club(async_client)
    quiz_resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz 1"})
    quiz_id = quiz_resp.json()["id"]
    q_resp = await async_client.post(f"/api/v1/quizzes/{quiz_id}/questions", headers=headers, json={"question": "Q1", "options": ["A", "B"], "correctIndex": 0})
    assert q_resp.status_code == 201
    assert q_resp.json()["question"] == "Q1"

@pytest.mark.asyncio
async def test_get_questions_as_organizer(async_client):
    headers, club_id = await create_organizer_with_club(async_client)
    quiz_resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz 1"})
    quiz_id = quiz_resp.json()["id"]
    await async_client.post(f"/api/v1/quizzes/{quiz_id}/questions", headers=headers, json={"question": "Q1", "options": ["A", "B"], "correctIndex": 0})
    resp = await async_client.get(f"/api/v1/quizzes/{quiz_id}/questions", headers=headers)
    assert resp.status_code == 200
    assert resp.json()[0]["correctIndex"] == 0

@pytest.mark.asyncio
async def test_get_questions_as_member(async_client):
    headers, club_id = await create_organizer_with_club(async_client)
    quiz_resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz 1"})
    quiz_id = quiz_resp.json()["id"]
    await async_client.post(f"/api/v1/quizzes/{quiz_id}/questions", headers=headers, json={"question": "Q1", "options": ["A", "B"], "correctIndex": 0})
    await register_user(async_client, email="user2@example.com")
    headers2 = await get_auth_headers(async_client, email="user2@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    resp = await async_client.get(f"/api/v1/quizzes/{quiz_id}/questions", headers=headers2)
    assert resp.status_code == 200
    assert "correctIndex" not in resp.json()[0] or resp.json()[0]["correctIndex"] is None

@pytest.mark.asyncio
async def test_activate_quiz(async_client):
    headers, club_id = await create_organizer_with_club(async_client)
    quiz_resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz 1"})
    quiz_id = quiz_resp.json()["id"]
    resp = await async_client.patch(f"/api/v1/quizzes/{quiz_id}/active", headers=headers, json={"isActive": True})
    assert resp.status_code == 200
    assert resp.json()["isActive"] is True

@pytest.mark.asyncio
async def test_submit_attempt(async_client):
    headers, club_id = await create_organizer_with_club(async_client)
    quiz_resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz 1"})
    quiz_id = quiz_resp.json()["id"]
    await async_client.post(f"/api/v1/quizzes/{quiz_id}/questions", headers=headers, json={"question": "Q1", "options": ["A", "B"], "correctIndex": 0})
    await async_client.patch(f"/api/v1/quizzes/{quiz_id}/active", headers=headers, json={"isActive": True})
    await register_user(async_client, email="user2@example.com")
    headers2 = await get_auth_headers(async_client, email="user2@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    attempt = {"answers": [0]}
    resp = await async_client.post(f"/api/v1/quizzes/{quiz_id}/attempts", headers=headers2, json=attempt)
    assert resp.status_code == 201
    assert "score" in resp.json()

@pytest.mark.asyncio
async def test_submit_attempt_inactive_quiz(async_client):
    headers, club_id = await create_organizer_with_club(async_client)
    quiz_resp = await async_client.post(f"/api/v1/clubs/{club_id}/quizzes", headers=headers, json={"title": "Quiz 1"})
    quiz_id = quiz_resp.json()["id"]
    await async_client.post(f"/api/v1/quizzes/{quiz_id}/questions", headers=headers, json={"question": "Q1", "options": ["A", "B"], "correctIndex": 0})
    await register_user(async_client, email="user2@example.com")
    headers2 = await get_auth_headers(async_client, email="user2@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=headers2)
    attempt = {"answers": [0]}
    resp = await async_client.post(f"/api/v1/quizzes/{quiz_id}/attempts", headers=headers2, json=attempt)
    assert resp.status_code == 403
