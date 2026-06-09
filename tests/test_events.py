from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FUTURE_DATE = "2099-12-31T10:00:00+00:00"
FUTURE_DATE_2 = "2099-11-15T14:00:00+00:00"

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
async def test_create_event_with_lat_lng(async_client, register_user, auth_headers):
    headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_coords@example.com")
    club_id = await _create_club(async_client, headers, "CoordsClub")
    event = await _create_event(async_client, headers, club_id, {**EVENT_PAYLOAD, "lat": 50.4501, "lng": 30.5234})
    assert event["lat"] == 50.4501
    assert event["lng"] == 30.5234


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
    # Two separate organizers so each can own one club (one-club-per-organizer limit)
    headers1 = await _setup_organizer(async_client, register_user, auth_headers, "ev_clubfilter1@example.com")
    headers2 = await _setup_organizer(async_client, register_user, auth_headers, "ev_clubfilter2@example.com")
    club_id1 = await _create_club(async_client, headers1, "FilterClub1")
    club_id2 = await _create_club(async_client, headers2, "FilterClub2")
    event1 = await _create_event(async_client, headers1, club_id1)
    await _create_event(async_client, headers2, club_id2)

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
async def test_list_my_events_returns_events_from_member_clubs(async_client, register_user, auth_headers, make_member):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_my_org@example.com")
    club_id = await _create_club(async_client, org_headers, "MyEvClub")
    event = await _create_event(async_client, org_headers, club_id)

    await register_user(email="ev_my_member@example.com")
    member_headers = await auth_headers(email="ev_my_member@example.com")
    await make_member(club_id, member_headers)

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


@pytest.mark.asyncio
async def test_attend_event_registration_closed(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_att_deadline@example.com")
    club_id = await _create_club(async_client, org_headers, "DeadlineClub")
    soon = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    event = await _create_event(async_client, org_headers, club_id, {**EVENT_PAYLOAD, "date": soon})

    await register_user(email="ev_att_deadline_m@example.com")
    member_headers = await auth_headers(email="ev_att_deadline_m@example.com")

    resp = await async_client.post(f"/api/v1/events/{event['id']}/attend", headers=member_headers)
    assert resp.status_code == 400
    body = resp.json()
    detail = body.get("detail", body)
    error_msg = detail.get("error", "") if isinstance(detail, dict) else str(detail)
    assert "Registration closed" in error_msg


@pytest.mark.asyncio
async def test_attend_event_does_not_auto_join_club(async_client, register_user, auth_headers):
    """Regression: POST /events/{id}/attend must NOT add the user to clubs/my.

    The user is a non-member before attending. After a successful RSVP the user
    should still not appear as a club member — attendance only creates a pending
    join request (joinRequestStatus == "pending"), never instant membership.
    """
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_att_nojoin_reg@example.com")
    club_id = await _create_club(async_client, org_headers, "NoAutoJoinClub")
    event = await _create_event(async_client, org_headers, club_id)

    await register_user(email="ev_att_nojoin_reg_m@example.com")
    member_headers = await auth_headers(email="ev_att_nojoin_reg_m@example.com")

    # RSVP succeeds
    resp = await async_client.post(f"/api/v1/events/{event['id']}/attend", headers=member_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["attendeeCount"] >= 1
    # autoJoined is removed; a non-member attending gets a pending join request
    assert "autoJoined" not in data
    assert data["joinRequestStatus"] == "pending"

    # User must NOT appear in club membership
    my_clubs_resp = await async_client.get("/api/v1/clubs/my", headers=member_headers)
    assert my_clubs_resp.status_code == 200
    my_club_ids = [c["id"] for c in my_clubs_resp.json()]
    assert club_id not in my_club_ids

    # Club member list must only contain the organizer
    members_resp = await async_client.get(f"/api/v1/clubs/{club_id}/members", headers=org_headers)
    assert members_resp.status_code == 200
    member_user_ids = [m["userId"] for m in members_resp.json()]
    # Organizer is the only member; the attendee must not be listed
    assert len(member_user_ids) == 1


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


# ---------------------------------------------------------------------------
# PATCH /api/v1/events/{event_id}  (update event)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_event_title(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_update1@example.com")
    club_id = await _create_club(async_client, org_headers, "UpdateClub1")
    event = await _create_event(async_client, org_headers, club_id)

    resp = await async_client.patch(
        f"/api/v1/events/{event['id']}",
        headers=org_headers,
        json={"title": "Updated Title"},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Updated Title"


@pytest.mark.asyncio
async def test_update_event_multiple_fields(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_update2@example.com")
    club_id = await _create_club(async_client, org_headers, "UpdateClub2")
    event = await _create_event(async_client, org_headers, club_id)

    resp = await async_client.patch(
        f"/api/v1/events/{event['id']}",
        headers=org_headers,
        json={"title": "New Title", "description": "New Desc", "city": "Lviv"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "New Title"
    assert data["description"] == "New Desc"
    assert data["city"] == "Lviv"


@pytest.mark.asyncio
async def test_update_event_with_after_meeting_venue(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_update3@example.com")
    club_id = await _create_club(async_client, org_headers, "UpdateClub3")
    event = await _create_event(async_client, org_headers, club_id)

    resp = await async_client.patch(
        f"/api/v1/events/{event['id']}",
        headers=org_headers,
        json={"after_meeting_venue": {"name": "Bar XYZ", "address": "Main St 1"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["afterMeetingVenue"]["name"] == "Bar XYZ"


@pytest.mark.asyncio
async def test_update_event_non_organizer_forbidden(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_update4_org@example.com")
    club_id = await _create_club(async_client, org_headers, "UpdateClub4")
    event = await _create_event(async_client, org_headers, club_id)

    await register_user(email="ev_update4_m@example.com")
    member_headers = await auth_headers(email="ev_update4_m@example.com")

    resp = await async_client.patch(
        f"/api/v1/events/{event['id']}",
        headers=member_headers,
        json={"title": "Hacked Title"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_event_not_found(async_client, register_user, auth_headers):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_update5@example.com")
    resp = await async_client.patch(
        f"/api/v1/events/{uuid.uuid4()}",
        headers=org_headers,
        json={"title": "Ghost"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/v1/events/{event_id}/winner  (set event winner)
# ---------------------------------------------------------------------------


async def _mark_event_held(db_session, event_id: str) -> None:
    """Directly set an event's status to 'held' in the DB (bypasses router logic)."""
    from sqlalchemy import update

    from app.models.event import Event

    await db_session.execute(update(Event).where(Event.id == uuid.UUID(event_id)).values(status="held"))
    await db_session.commit()


@pytest.mark.asyncio
async def test_set_event_winner_happy_path(async_client, register_user, auth_headers, db_session):
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_winner1@example.com")
    club_id = await _create_club(async_client, org_headers, "WinnerClub1")
    event = await _create_event(async_client, org_headers, club_id)

    # Add a regular attendee
    await register_user(email="ev_winner1_m@example.com", displayName="Champion User")
    member_headers = await auth_headers(email="ev_winner1_m@example.com")
    await async_client.post(f"/api/v1/events/{event['id']}/attend", headers=member_headers)
    me_resp = await async_client.get("/api/v1/users/me", headers=member_headers)
    winner_user_id = me_resp.json()["id"]

    # Mark event as held directly in DB
    await _mark_event_held(db_session, event["id"])

    resp = await async_client.patch(
        f"/api/v1/events/{event['id']}/winner",
        headers=org_headers,
        json={"winner_id": winner_user_id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["hasWinner"] is True
    assert data["winnerId"] == winner_user_id
    assert data["winnerName"] == "Champion User"


@pytest.mark.asyncio
async def test_set_event_winner_event_not_held(async_client, register_user, auth_headers):
    """Returns 400 EVENT_NOT_HELD when event status is not 'held'."""
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_winner_notheld@example.com")
    club_id = await _create_club(async_client, org_headers, "WinnerClub2")
    event = await _create_event(async_client, org_headers, club_id)

    await register_user(email="ev_winner_notheld_m@example.com")
    member_headers = await auth_headers(email="ev_winner_notheld_m@example.com")
    me_resp = await async_client.get("/api/v1/users/me", headers=member_headers)
    some_user_id = me_resp.json()["id"]

    resp = await async_client.patch(
        f"/api/v1/events/{event['id']}/winner",
        headers=org_headers,
        json={"winner_id": some_user_id},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "EVENT_NOT_HELD"


@pytest.mark.asyncio
async def test_set_event_winner_winner_not_attendee(async_client, register_user, auth_headers, db_session):
    """Returns 400 WINNER_NOT_ATTENDEE when winner_id did not attend the event."""
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_winner_noatt@example.com")
    club_id = await _create_club(async_client, org_headers, "WinnerClub3")
    event = await _create_event(async_client, org_headers, club_id)

    await register_user(email="ev_winner_noatt_m@example.com")
    member_headers = await auth_headers(email="ev_winner_noatt_m@example.com")
    me_resp = await async_client.get("/api/v1/users/me", headers=member_headers)
    non_attendee_id = me_resp.json()["id"]

    await _mark_event_held(db_session, event["id"])

    resp = await async_client.patch(
        f"/api/v1/events/{event['id']}/winner",
        headers=org_headers,
        json={"winner_id": non_attendee_id},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "WINNER_NOT_ATTENDEE"


@pytest.mark.asyncio
async def test_set_event_winner_event_not_found(async_client, register_user, auth_headers):
    """Returns 404 when event does not exist."""
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_winner_nf@example.com")
    resp = await async_client.patch(
        f"/api/v1/events/{uuid.uuid4()}/winner",
        headers=org_headers,
        json={"winner_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_set_event_winner_unauthenticated(async_client):
    """Returns 401 when no token is provided."""
    resp = await async_client.patch(
        f"/api/v1/events/{uuid.uuid4()}/winner",
        json={"winner_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_set_event_winner_non_organizer_forbidden(async_client, register_user, auth_headers, db_session):
    """Returns 403 when a regular member tries to set a winner."""
    org_headers = await _setup_organizer(async_client, register_user, auth_headers, "ev_winner_forbid@example.com")
    club_id = await _create_club(async_client, org_headers, "WinnerClub4")
    event = await _create_event(async_client, org_headers, club_id)

    await register_user(email="ev_winner_forbid_m@example.com")
    member_headers = await auth_headers(email="ev_winner_forbid_m@example.com")
    await async_client.post(f"/api/v1/events/{event['id']}/attend", headers=member_headers)
    me_resp = await async_client.get("/api/v1/users/me", headers=member_headers)
    member_id = me_resp.json()["id"]

    await _mark_event_held(db_session, event["id"])

    resp = await async_client.patch(
        f"/api/v1/events/{event['id']}/winner",
        headers=member_headers,
        json={"winner_id": member_id},
    )
    assert resp.status_code == 403
