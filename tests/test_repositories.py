"""Unit tests for the repository layer — directly exercises DB without HTTP."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.chat import ChatMessage, ChatRoom
from app.models.club import Club
from app.models.club_ban import ClubBan
from app.models.club_member import ClubMember
from app.models.event import Event, EventAttendee
from app.models.quiz import Quiz, QuizAttempt, QuizQuestion, QuizSession
from app.models.user import User
from app.repositories import ChatRepository, ClubRepository, EventRepository, QuizRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(**kwargs) -> User:
    uid = uuid.uuid4()
    defaults: dict = {
        "id": uid,
        "supabase_user_id": uuid.uuid4(),
        "email": f"{uid}@test.com",
        "display_name": "Test User",
        "role": "user",
    }
    defaults.update(kwargs)
    return User(**defaults)


def _club(organizer_id: uuid.UUID, **kwargs) -> Club:
    defaults: dict = {
        "id": uuid.uuid4(),
        "name": "Test Club",
        "organizer_id": organizer_id,
        "is_public": True,
        "status": "active",
        "city": "Kyiv",
        "tags": [],
    }
    defaults.update(kwargs)
    return Club(**defaults)


def _event(club_id: uuid.UUID, date_offset_days: int = 1, **kwargs) -> Event:
    defaults: dict = {
        "id": uuid.uuid4(),
        "club_id": club_id,
        "title": "Test Event",
        "date": datetime.now(UTC) + timedelta(days=date_offset_days),
        "city": "Kyiv",
        "status": "scheduled",
        "tags": [],
    }
    defaults.update(kwargs)
    return Event(**defaults)


# ---------------------------------------------------------------------------
# ClubRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_club_repo_get_by_id(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    await db_session.commit()

    repo = ClubRepository(db_session)
    found = await repo.get_by_id(club.id)
    assert found is not None
    assert found.id == club.id

    missing = await repo.get_by_id(uuid.uuid4())
    assert missing is None


@pytest.mark.asyncio
async def test_club_repo_list_public_or_member(db_session):
    user = _user()
    db_session.add(user)
    public_club = _club(organizer_id=user.id, name="Public", is_public=True)
    private_club = _club(organizer_id=user.id, name="Private", is_public=False)
    db_session.add_all([public_club, private_club])
    await db_session.commit()

    repo = ClubRepository(db_session)

    # Unauthenticated — only public
    clubs = await repo.list_public_or_member()
    names = [c.name for c in clubs]
    assert "Public" in names
    assert "Private" not in names

    # Member sees private too
    member = ClubMember(id=uuid.uuid4(), club_id=private_club.id, user_id=user.id, role="organizer")
    db_session.add(member)
    await db_session.commit()

    clubs_auth = await repo.list_public_or_member(user_id=user.id)
    names_auth = [c.name for c in clubs_auth]
    assert "Private" in names_auth


@pytest.mark.asyncio
async def test_club_repo_list_public_search(db_session):
    user = _user()
    db_session.add(user)
    db_session.add(_club(organizer_id=user.id, name="Dragon Readers"))
    db_session.add(_club(organizer_id=user.id, name="Book Lovers"))
    await db_session.commit()

    repo = ClubRepository(db_session)
    results = await repo.list_public_or_member(search="Dragon")
    assert len(results) == 1
    assert results[0].name == "Dragon Readers"

    empty = await repo.list_public_or_member(search="zzznomatch")
    assert empty == []


@pytest.mark.asyncio
async def test_club_repo_list_for_user(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    member = ClubMember(id=uuid.uuid4(), club_id=club.id, user_id=user.id, role="organizer")
    db_session.add(member)
    await db_session.commit()

    repo = ClubRepository(db_session)
    clubs = await repo.list_for_user(user.id)
    assert any(c.id == club.id for c in clubs)


@pytest.mark.asyncio
async def test_club_repo_count_members(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    member = ClubMember(id=uuid.uuid4(), club_id=club.id, user_id=user.id, role="organizer")
    db_session.add(member)
    await db_session.commit()

    repo = ClubRepository(db_session)
    assert await repo.count_members(club.id) == 1
    assert await repo.count_members(uuid.uuid4()) == 0


@pytest.mark.asyncio
async def test_club_repo_member_avatar_previews(db_session):
    user = _user(avatar_url="https://example.com/avatar.png")
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    member = ClubMember(id=uuid.uuid4(), club_id=club.id, user_id=user.id, role="organizer")
    db_session.add(member)
    await db_session.commit()

    repo = ClubRepository(db_session)
    previews = await repo.get_member_avatar_previews(club.id)
    assert "https://example.com/avatar.png" in previews


@pytest.mark.asyncio
async def test_club_repo_is_banned(db_session):
    organizer = _user()
    target = _user()
    db_session.add_all([organizer, target])
    club = _club(organizer_id=organizer.id)
    db_session.add(club)
    await db_session.commit()

    repo = ClubRepository(db_session)
    assert not await repo.is_banned(club.id, target.id)

    ban = ClubBan(
        id=uuid.uuid4(),
        club_id=club.id,
        user_id=target.id,
        banned_by=organizer.id,
        duration="permanent",
    )
    db_session.add(ban)
    await db_session.commit()

    assert await repo.is_banned(club.id, target.id)


@pytest.mark.asyncio
async def test_club_repo_membership(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    await db_session.commit()

    repo = ClubRepository(db_session)
    assert await repo.get_membership(club.id, user.id) is None

    member = ClubMember(id=uuid.uuid4(), club_id=club.id, user_id=user.id, role="member")
    await repo.add_member(member)
    await db_session.commit()

    assert await repo.get_membership(club.id, user.id) is not None

    await repo.remove_member(club.id, user.id)
    await db_session.commit()

    assert await repo.get_membership(club.id, user.id) is None


# ---------------------------------------------------------------------------
# EventRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_repo_get_by_id(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    event = _event(club_id=club.id)
    db_session.add(event)
    await db_session.commit()

    repo = EventRepository(db_session)
    assert (await repo.get_by_id(event.id)) is not None
    assert (await repo.get_by_id(uuid.uuid4())) is None


@pytest.mark.asyncio
async def test_event_repo_list_upcoming(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    future = _event(club_id=club.id, date_offset_days=2, title="Future")
    past = _event(club_id=club.id, date_offset_days=-2, title="Past")
    db_session.add_all([future, past])
    await db_session.commit()

    repo = EventRepository(db_session)
    now = datetime.now(UTC)
    upcoming = await repo.list_upcoming(now)
    titles = [e.title for e in upcoming]
    assert "Future" in titles
    assert "Past" not in titles


@pytest.mark.asyncio
async def test_event_repo_list_upcoming_city_filter(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    db_session.add(_event(club_id=club.id, title="Kyiv Event", city="Kyiv"))
    db_session.add(_event(club_id=club.id, title="Lviv Event", city="Lviv"))
    await db_session.commit()

    repo = EventRepository(db_session)
    results = await repo.list_upcoming(datetime.now(UTC), city="Kyiv")
    assert all(e.city == "Kyiv" for e in results)


@pytest.mark.asyncio
async def test_event_repo_list_upcoming_club_filter(db_session):
    user = _user()
    db_session.add(user)
    club1 = _club(organizer_id=user.id, name="Club1")
    club2 = _club(organizer_id=user.id, name="Club2")
    db_session.add_all([club1, club2])
    db_session.add(_event(club_id=club1.id, title="C1 Event"))
    db_session.add(_event(club_id=club2.id, title="C2 Event"))
    await db_session.commit()

    repo = EventRepository(db_session)
    results = await repo.list_upcoming(datetime.now(UTC), club_id=club1.id)
    assert all(e.club_id == club1.id for e in results)


@pytest.mark.asyncio
async def test_event_repo_list_for_member(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    member = ClubMember(id=uuid.uuid4(), club_id=club.id, user_id=user.id, role="organizer")
    db_session.add(member)
    future = _event(club_id=club.id, date_offset_days=1)
    db_session.add(future)
    await db_session.commit()

    repo = EventRepository(db_session)
    events = await repo.list_for_member(user.id, datetime.now(UTC))
    assert any(e.id == future.id for e in events)


@pytest.mark.asyncio
async def test_event_repo_list_for_club(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    e1 = _event(club_id=club.id, date_offset_days=1)
    e2 = _event(club_id=club.id, date_offset_days=-1)
    db_session.add_all([e1, e2])
    await db_session.commit()

    repo = EventRepository(db_session)
    all_events = await repo.list_for_club(club.id)
    assert len(all_events) == 2

    upcoming = await repo.list_for_club(club.id, upcoming_only=True, now=datetime.now(UTC))
    assert len(upcoming) == 1


@pytest.mark.asyncio
async def test_event_repo_attendees(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    event = _event(club_id=club.id)
    db_session.add(event)
    await db_session.commit()

    repo = EventRepository(db_session)
    assert await repo.count_attendees(event.id) == 0
    assert await repo.get_attendance(event.id, user.id) is None

    attendee = EventAttendee(event_id=event.id, user_id=user.id)
    await repo.add_attendee(attendee)
    await db_session.commit()

    assert await repo.count_attendees(event.id) == 1
    assert await repo.get_attendance(event.id, user.id) is not None

    await repo.remove_attendee(event.id, user.id)
    await db_session.commit()

    assert await repo.count_attendees(event.id) == 0


@pytest.mark.asyncio
async def test_event_repo_get_club_for_event(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    event = _event(club_id=club.id)
    db_session.add(event)
    await db_session.commit()

    repo = EventRepository(db_session)
    found_club = await repo.get_club_for_event(event)
    assert found_club is not None
    assert found_club.id == club.id


# ---------------------------------------------------------------------------
# ChatRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_repo_rooms(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    room = ChatRoom(id=uuid.uuid4(), club_id=club.id, name="General")
    db_session.add(room)
    await db_session.commit()

    repo = ChatRepository(db_session)
    assert await repo.get_room(room.id) is not None
    assert await repo.get_room(uuid.uuid4()) is None

    rooms = await repo.list_rooms(club.id)
    assert any(r.id == room.id for r in rooms)
    assert await repo.list_rooms(uuid.uuid4()) == []


@pytest.mark.asyncio
async def test_chat_repo_messages(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    room = ChatRoom(id=uuid.uuid4(), club_id=club.id, name="General")
    db_session.add(room)
    await db_session.commit()

    repo = ChatRepository(db_session)
    assert await repo.get_message(uuid.uuid4(), room.id) is None

    msg = ChatMessage(
        id=uuid.uuid4(),
        room_id=room.id,
        sender_id=user.id,
        text="Hello",
        timestamp=datetime.now(UTC),
    )
    db_session.add(msg)
    await db_session.commit()

    found = await repo.get_message(msg.id, room.id)
    assert found is not None

    ts = await repo.get_message_timestamp(msg.id)
    assert ts is not None

    messages = await repo.list_messages(room.id)
    assert len(messages) == 1
    assert messages[0][1] == user.display_name


@pytest.mark.asyncio
async def test_chat_repo_list_messages_before_ts(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    room = ChatRoom(id=uuid.uuid4(), club_id=club.id, name="General")
    db_session.add(room)
    now = datetime.now(UTC)
    old_msg = ChatMessage(
        id=uuid.uuid4(), room_id=room.id, sender_id=user.id, text="Old", timestamp=now - timedelta(hours=2)
    )
    new_msg = ChatMessage(id=uuid.uuid4(), room_id=room.id, sender_id=user.id, text="New", timestamp=now)
    db_session.add_all([old_msg, new_msg])
    await db_session.commit()

    repo = ChatRepository(db_session)
    before = await repo.list_messages(room.id, before_ts=now - timedelta(hours=1))
    assert len(before) == 1
    assert before[0][0].text == "Old"


# ---------------------------------------------------------------------------
# QuizRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quiz_repo_get_by_id(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    quiz = Quiz(id=uuid.uuid4(), club_id=club.id, created_by=user.id, title="Quiz 1")
    db_session.add(quiz)
    await db_session.commit()

    repo = QuizRepository(db_session)
    assert await repo.get_by_id(quiz.id) is not None
    assert await repo.get_by_id(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_quiz_repo_list_for_club(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    q1 = Quiz(id=uuid.uuid4(), club_id=club.id, created_by=user.id, title="Q1")
    q2 = Quiz(id=uuid.uuid4(), club_id=club.id, created_by=user.id, title="Q2")
    db_session.add_all([q1, q2])
    await db_session.commit()

    repo = QuizRepository(db_session)
    quizzes = await repo.list_for_club(club.id)
    assert len(quizzes) == 2
    assert await repo.list_for_club(uuid.uuid4()) == []


@pytest.mark.asyncio
async def test_quiz_repo_questions(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    quiz = Quiz(id=uuid.uuid4(), club_id=club.id, created_by=user.id, title="Quiz")
    db_session.add(quiz)
    q = QuizQuestion(id=uuid.uuid4(), quiz_id=quiz.id, question="Q?", options=["A", "B"], correct_index=0, position=1)
    db_session.add(q)
    await db_session.commit()

    repo = QuizRepository(db_session)
    questions = await repo.get_questions(quiz.id)
    assert len(questions) == 1

    found = await repo.get_question(q.id, quiz.id)
    assert found is not None
    assert await repo.get_question(uuid.uuid4(), quiz.id) is None

    assert await repo.count_questions(quiz.id) == 1
    assert await repo.count_questions(uuid.uuid4()) == 0


@pytest.mark.asyncio
async def test_quiz_repo_sessions(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    quiz = Quiz(id=uuid.uuid4(), club_id=club.id, created_by=user.id, title="Quiz")
    db_session.add(quiz)
    await db_session.commit()

    repo = QuizRepository(db_session)
    assert await repo.get_active_session(quiz.id) is None

    session = QuizSession(id=uuid.uuid4(), quiz_id=quiz.id, started_by=user.id)
    db_session.add(session)
    await db_session.commit()

    active = await repo.get_active_session(quiz.id)
    assert active is not None

    found = await repo.get_session(session.id, quiz.id)
    assert found is not None
    assert await repo.get_session(uuid.uuid4(), quiz.id) is None


@pytest.mark.asyncio
async def test_quiz_repo_attempts(db_session):
    user = _user()
    db_session.add(user)
    club = _club(organizer_id=user.id)
    db_session.add(club)
    quiz = Quiz(id=uuid.uuid4(), club_id=club.id, created_by=user.id, title="Quiz")
    db_session.add(quiz)
    await db_session.commit()

    repo = QuizRepository(db_session)
    assert await repo.count_attempts(quiz.id) == 0

    attempt = QuizAttempt(id=uuid.uuid4(), quiz_id=quiz.id, user_id=user.id, score=3, total=5, answers=[0, 1, 0, 1, 0])
    db_session.add(attempt)
    await db_session.commit()

    assert await repo.count_attempts(quiz.id) == 1

    rows = await repo.get_attempts_with_users(quiz.id)
    assert len(rows) == 1
    attempt_obj, display_name, avatar_url = rows[0]
    assert attempt_obj.score == 3
    assert display_name == user.display_name
