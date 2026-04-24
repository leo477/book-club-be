from __future__ import annotations

import uuid

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FUTURE_DATE = "2099-12-31T10:00:00"
FUTURE_DATE_2 = "2099-11-15T14:00:00"

EVENT_PAYLOAD = {
    "title": "Book Night",
    "date": FUTURE_DATE,
    "city": "Kyiv",
    "description": "Read and discuss",
}


async def _setup_organizer(async_client, register_user, auth_headers, email: str) -> dict:
    await register_user(email=email)
    headers = await auth_headers(email=email)
    await async_client.patch("/api/v1/users/me/role", headers=headers, json={"role": "organizer"})
    return headers


async def _create_club(async_client, headers: dict, name: str = "Test Club") -> str:
    resp = await async_client.post(
        "/api/v1/clubs",
        headers=headers,
        json={"name": name, "description": "Desc"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_event(async_client, headers: dict, club_id: str, payload: dict | None = None) -> dict:
    body = payload or EVENT_PAYLOAD
    resp = await async_client.post(f"/api/v1/clubs/{club_id}/events", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# GET /api/v1/events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_events_empty(async_client):
    resp = await async_client.get("/api/v1/events")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_events_returns_upcoming(async_client, register_user, auth_headers):
    headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_list1@example.com")
    club_id = await _create_club(async_client, headers, "EvClub1")
    event = await _create_event(async_client, headers, club_id)

    resp = await async_client.get("/api/v1/events")
    assert resp.status_code == 200
    ids = [e["id"] for e in resp.json()]
    assert event["id"] in ids


@pytest.mark.asyncio
async def test_list_events_filter_by_city(async_client, register_user, auth_headers):
    headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_city@example.com")
    club_id = await _create_club(async_client, headers, "CityClub")
    await _create_event(async_client, headers, club_id, {**EVENT_PAYLOAD, "city": "Lviv"})
    await _create_event(async_client, headers, club_id, {**EVENT_PAYLOAD, "title": "Odesa Night", "city": "Odesa"})

    resp = await async_client.get("/api/v1/events?city=Lviv")
    assert resp.status_code == 200
    cities = [e["city"] for e in resp.json()]
    assert all(c == "Lviv" for c in cities)
    assert "Odesa" not in cities


@pytest.mark.asyncio
async def test_list_events_filter_by_club_id(async_client, register_user, auth_headers):
    headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_clubfilter@example.com")
    club_id1 = await _create_club(async_client, headers, "FilterClub1")
    club_id2 = await _create_club(async_client, headers, "FilterClub2")
    event1 = await _create_event(async_client, headers, club_id1)
    await _create_event(async_client, headers, club_id2)

    resp = await async_client.get(f"/api/v1/events?club_id={club_id1}")
    assert resp.status_code == 200
    ids = [e["id"] for e in resp.json()]
    assert event1["id"] in ids
    assert all(e["clubId"] == club_id1 for e in resp.json())


@pytest.mark.asyncio
async def test_list_events_unauthenticated(async_client, register_user, auth_headers):
    headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_unauth@example.com")
    club_id = await _create_club(async_client, headers, "UnauthClub")
    await _create_event(async_client, headers, club_id)

    resp = await async_client.get("/api/v1/events")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# GET /api/v1/events/my
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_my_events_unauthenticated(async_client):
    resp = await async_client.get("/api/v1/events/my")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_my_events_empty_when_no_clubs(async_client, register_user, auth_headers):
    await register_user(email="ev_my_empty@example.com")
    headers = await auth_headers(email="ev_my_empty@example.com")
    resp = await async_client.get("/api/v1/events/my", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_my_events_returns_events_from_member_clubs(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_my_org@example.com")
    club_id = await _create_club(async_client, org_headers, "MyEvClub")
    event = await _create_event(async_client, org_headers, club_id)

    await register_user(email="ev_my_member@example.com")
    member_headers = await auth_headers(email="ev_my_member@example.com")
    await async_client.post(f"/api/v1/clubs/{club_id}/join", headers=member_headers)

    resp = await async_client.get("/api/v1/events/my", headers=member_headers)
    assert resp.status_code == 200
    ids = [e["id"] for e in resp.json()]
    assert event["id"] in ids


# ---------------------------------------------------------------------------
# GET /api/v1/events/{event_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_event_by_id(async_client, register_user, auth_headers):
    headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_get@example.com")
    club_id = await _create_club(async_client, headers, "GetEvClub")
    event = await _create_event(async_client, headers, club_id)

    resp = await async_client.get(f"/api/v1/events/{event['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == event["id"]
    assert resp.json()["title"] == event["title"]


@pytest.mark.asyncio
async def test_get_event_not_found(async_client):
    fake_id = str(uuid.uuid4())
    resp = await async_client.get(f"/api/v1/events/{fake_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_event_authenticated_shows_is_attending(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_attend_get@example.com")
    club_id = await _create_club(async_client, org_headers, "AttendGetClub")
    event = await _create_event(async_client, org_headers, club_id)

    await register_user(email="ev_attend_get_m@example.com")
    member_headers = await auth_headers(email="ev_attend_get_m@example.com")
    await async_client.post(f"/api/v1/events/{event['id']}/attend", headers=member_headers)

    resp = await async_client.get(f"/api/v1/events/{event['id']}", headers=member_headers)
    assert resp.status_code == 200
    assert resp.json()["isAttending"] is True


# ---------------------------------------------------------------------------
# POST /api/v1/events/{event_id}/attend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attend_event(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_att1@example.com")
    club_id = await _create_club(async_client, org_headers, "AttClub1")
    event = await _create_event(async_client, org_headers, club_id)

    await register_user(email="ev_att1_m@example.com")
    member_headers = await auth_headers(email="ev_att1_m@example.com")

    resp = await async_client.post(f"/api/v1/events/{event['id']}/attend", headers=member_headers)
    assert resp.status_code == 201
    assert resp.json()["attendeeCount"] >= 1


@pytest.mark.asyncio
async def test_attend_event_unauthenticated(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_att_unauth@example.com")
    club_id = await _create_club(async_client, org_headers, "AttClubUnauth")
    event = await _create_event(async_client, org_headers, club_id)

    resp = await async_client.post(f"/api/v1/events/{event['id']}/attend")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_attend_event_already_attending(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_att2@example.com")
    club_id = await _create_club(async_client, org_headers, "AttClub2")
    event = await _create_event(async_client, org_headers, club_id)

    await register_user(email="ev_att2_m@example.com")
    member_headers = await auth_headers(email="ev_att2_m@example.com")
    await async_client.post(f"/api/v1/events/{event['id']}/attend", headers=member_headers)

    resp = await async_client.post(f"/api/v1/events/{event['id']}/attend", headers=member_headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_attend_cancelled_event(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_att_cancel@example.com")
    club_id = await _create_club(async_client, org_headers, "AttCancelClub")
    event = await _create_event(async_client, org_headers, club_id)

    await async_client.patch(f"/api/v1/events/{event['id']}/cancel", headers=org_headers)

    await register_user(email="ev_att_cancel_m@example.com")
    member_headers = await auth_headers(email="ev_att_cancel_m@example.com")

    resp = await async_client.post(f"/api/v1/events/{event['id']}/attend", headers=member_headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_attend_event_not_found(async_client, register_user, auth_headers):
    await register_user(email="ev_att_nf@example.com")
    headers = await auth_headers(email="ev_att_nf@example.com")
    resp = await async_client.post(f"/api/v1/events/{uuid.uuid4()}/attend", headers=headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/v1/events/{event_id}/attend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_attendance(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_catt1@example.com")
    club_id = await _create_club(async_client, org_headers, "CAttClub1")
    event = await _create_event(async_client, org_headers, club_id)

    await register_user(email="ev_catt1_m@example.com")
    member_headers = await auth_headers(email="ev_catt1_m@example.com")
    await async_client.post(f"/api/v1/events/{event['id']}/attend", headers=member_headers)

    resp = await async_client.delete(f"/api/v1/events/{event['id']}/attend", headers=member_headers)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_cancel_attendance_when_not_attending(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_catt2@example.com")
    club_id = await _create_club(async_client, org_headers, "CAttClub2")
    event = await _create_event(async_client, org_headers, club_id)

    await register_user(email="ev_catt2_m@example.com")
    member_headers = await auth_headers(email="ev_catt2_m@example.com")

    resp = await async_client.delete(f"/api/v1/events/{event['id']}/attend", headers=member_headers)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_cancel_attendance_event_not_found(async_client, register_user, auth_headers):
    await register_user(email="ev_catt_nf@example.com")
    headers = await auth_headers(email="ev_catt_nf@example.com")
    resp = await async_client.delete(f"/api/v1/events/{uuid.uuid4()}/attend", headers=headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/v1/events/{event_id}/reschedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reschedule_event(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_reschedule@example.com")
    club_id = await _create_club(async_client, org_headers, "RescheduleClub")
    event = await _create_event(async_client, org_headers, club_id)

    resp = await async_client.patch(
        f"/api/v1/events/{event['id']}/reschedule",
        headers=org_headers,
        json={"newDate": FUTURE_DATE_2, "newCity": "Lviv", "newAddress": "Svobody Ave"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rescheduled"
    assert data["city"] == "Lviv"
    assert data["address"] == "Svobody Ave"


@pytest.mark.asyncio
async def test_reschedule_event_non_organizer(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_reschedule_org@example.com")
    club_id = await _create_club(async_client, org_headers, "RescheduleClub2")
    event = await _create_event(async_client, org_headers, club_id)

    await register_user(email="ev_reschedule_m@example.com")
    member_headers = await auth_headers(email="ev_reschedule_m@example.com")

    resp = await async_client.patch(
        f"/api/v1/events/{event['id']}/reschedule",
        headers=member_headers,
        json={"newDate": FUTURE_DATE_2},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_reschedule_event_not_found(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_reschedule_nf@example.com")
    resp = await async_client.patch(
        f"/api/v1/events/{uuid.uuid4()}/reschedule",
        headers=org_headers,
        json={"newDate": FUTURE_DATE_2},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/v1/events/{event_id}/cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_event(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_cancel1@example.com")
    club_id = await _create_club(async_client, org_headers, "CancelClub1")
    event = await _create_event(async_client, org_headers, club_id)

    resp = await async_client.patch(f"/api/v1/events/{event['id']}/cancel", headers=org_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"
    assert data["cancelledAt"] is not None


@pytest.mark.asyncio
async def test_cancel_event_non_organizer(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_cancel2_org@example.com")
    club_id = await _create_club(async_client, org_headers, "CancelClub2")
    event = await _create_event(async_client, org_headers, club_id)

    await register_user(email="ev_cancel2_m@example.com")
    member_headers = await auth_headers(email="ev_cancel2_m@example.com")

    resp = await async_client.patch(f"/api/v1/events/{event['id']}/cancel", headers=member_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cancel_event_not_found(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_cancel_nf@example.com")
    resp = await async_client.patch(f"/api/v1/events/{uuid.uuid4()}/cancel", headers=org_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/clubs/{club_id}/events  (create event via clubs router)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_event_as_organizer(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_create1@example.com")
    club_id = await _create_club(async_client, org_headers, "CreateEvClub")

    resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/events",
        headers=org_headers,
        json={
            "title": "Discussion Night",
            "date": FUTURE_DATE,
            "city": "Kyiv",
            "description": "Monthly book discussion",
            "tags": ["fiction", "classic"],
            "durationMinutes": 90,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Discussion Night"
    assert data["clubId"] == club_id
    assert data["status"] == "scheduled"
    assert data["tags"] == ["fiction", "classic"]


@pytest.mark.asyncio
async def test_create_event_non_organizer_forbidden(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_create2_org@example.com")
    club_id = await _create_club(async_client, org_headers, "CreateEvClub2")

    await register_user(email="ev_create2_m@example.com")
    member_headers = await auth_headers(email="ev_create2_m@example.com")

    resp = await async_client.post(
        f"/api/v1/clubs/{club_id}/events",
        headers=member_headers,
        json={"title": "Hack", "date": FUTURE_DATE, "city": "Kyiv"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_event_club_not_found(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_create_nf@example.com")
    resp = await async_client.post(
        f"/api/v1/clubs/{uuid.uuid4()}/events",
        headers=org_headers,
        json={"title": "Ghost Event", "date": FUTURE_DATE, "city": "Kyiv"},
    )
    assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# GET /api/v1/clubs/{club_id}/events  (list club events via clubs router)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_club_events_empty(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_club_list@example.com")
    club_id = await _create_club(async_client, org_headers, "ClubListEvClub")

    resp = await async_client.get(f"/api/v1/clubs/{club_id}/events")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_club_events_with_upcoming_filter(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_club_upcom@example.com")
    club_id = await _create_club(async_client, org_headers, "UpcomClub")
    await _create_event(async_client, org_headers, club_id)

    resp = await async_client.get(f"/api/v1/clubs/{club_id}/events?upcoming_only=true")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
