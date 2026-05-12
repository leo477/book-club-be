from __future__ import annotations

import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.chat import ChatMessage, ChatRoom, ChatRoomBan
from app.models.club import Club
from app.models.club_ban import ClubBan
from app.models.club_member import ClubMember
from app.models.quiz import QuizAttempt
from app.models.user import User
from app.schemas.clubs import ClubResponse
from app.schemas.users import UserStatsResponse


async def delete_club_cascade(club_id: uuid.UUID, db: AsyncSession) -> None:
    room_ids_result = await db.execute(select(ChatRoom.id).where(ChatRoom.club_id == club_id))
    room_ids = list(room_ids_result.scalars().all())
    if room_ids:
        await db.execute(sa_delete(ChatRoomBan).where(ChatRoomBan.room_id.in_(room_ids)))
        await db.execute(sa_delete(ChatMessage).where(ChatMessage.room_id.in_(room_ids)))
        await db.execute(sa_delete(ChatRoom).where(ChatRoom.club_id == club_id))
    await db.execute(sa_delete(ClubBan).where(ClubBan.club_id == club_id))
    await db.execute(sa_delete(ClubMember).where(ClubMember.club_id == club_id))

    from app.models.event import Event, EventAttendee

    event_ids_result = await db.execute(select(Event.id).where(Event.club_id == club_id))
    event_ids = list(event_ids_result.scalars().all())
    if event_ids:
        await db.execute(sa_delete(EventAttendee).where(EventAttendee.event_id.in_(event_ids)))
        await db.execute(sa_delete(Event).where(Event.club_id == club_id))

    club_result = await db.execute(select(Club).where(Club.id == club_id))
    club = club_result.scalar_one_or_none()
    if club:
        await db.delete(club)
    await db.commit()


async def get_club_or_404(club_id: uuid.UUID, db: AsyncSession) -> Club:
    result = await db.execute(select(Club).where(Club.id == club_id))
    club = result.scalar_one_or_none()
    if not club:
        raise AppError(404, "Club not found", "CLUB_NOT_FOUND")
    return club


async def get_user_stats(user_id: uuid.UUID, db: AsyncSession) -> UserStatsResponse:
    clubs_result = await db.execute(select(func.count()).select_from(ClubMember).where(ClubMember.user_id == user_id))
    quizzes_result = await db.execute(
        select(func.count()).select_from(QuizAttempt).where(QuizAttempt.user_id == user_id)
    )
    wins_result = await db.execute(
        select(func.count())
        .select_from(QuizAttempt)
        .where(QuizAttempt.user_id == user_id, QuizAttempt.score == QuizAttempt.total)
    )
    return UserStatsResponse(
        clubsJoined=clubs_result.scalar() or 0,
        quizzesTaken=quizzes_result.scalar() or 0,
        quizWins=wins_result.scalar() or 0,
        likesReceived=0,
        booksRead=0,
    )


async def build_club_response(club: Club, db: AsyncSession) -> ClubResponse:
    count_result = await db.execute(select(func.count()).select_from(ClubMember).where(ClubMember.club_id == club.id))
    member_count = count_result.scalar() or 0

    members_result = await db.execute(
        select(User.avatar_url)
        .join(ClubMember, ClubMember.user_id == User.id)
        .where(ClubMember.club_id == club.id)
        .limit(5)
    )
    previews = [r for r in members_result.scalars() if r]
    return _assemble_club_response(club, member_count, previews)


async def build_club_responses_bulk(clubs: list[Club], db: AsyncSession) -> list[ClubResponse]:
    if not clubs:
        return []

    club_ids = [club.id for club in clubs]

    # 1. Отримуємо кількість учасників для всіх клубів одним запитом
    counts_result = await db.execute(
        select(ClubMember.club_id, func.count(ClubMember.user_id))
        .where(ClubMember.club_id.in_(club_ids))
        .group_by(ClubMember.club_id)
    )
    counts_map = dict(counts_result.all())

    subq = (
        select(
            ClubMember.club_id,
            User.avatar_url,
            func.row_number()
            .over(
                partition_by=ClubMember.club_id,
                order_by=ClubMember.joined_at.desc(),
            )
            .label("rn"),
        )
        .join(User, User.id == ClubMember.user_id)
        .where(ClubMember.club_id.in_(club_ids))
        .subquery()
    )

    previews_result = await db.execute(select(subq.c.club_id, subq.c.avatar_url).where(subq.c.rn <= 5))

    previews_map = {}
    for row in previews_result.all():
        cid, url = row
        if url:
            previews_map.setdefault(cid, []).append(url)

    # 3. Збираємо фінальний список за допомогою вашої функції _assemble_club_response
    responses = []
    for club in clubs:
        responses.append(
            _assemble_club_response(
                club=club, member_count=counts_map.get(club.id, 0), previews=previews_map.get(club.id, [])
            )
        )

    return responses


def _assemble_club_response(club: Club, member_count: int, previews: list[str]) -> ClubResponse:
    return ClubResponse(
        id=club.id,
        name=club.name,
        description=club.description,
        # Додайте інші поля вашої моделі Club
        memberCount=member_count,
        memberPreviews=previews,
        # Наприклад, якщо є поле owner_id
        ownerId=club.owner_id if hasattr(club, "owner_id") else None,
    )
