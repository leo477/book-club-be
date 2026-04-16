import uuid
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db_dep
from app.models.club import Club
from app.models.meeting import Meeting, MeetingAttendee
from app.models.user import User
from app.schemas.quizzes import MeetingResponse

router = APIRouter(prefix="/api/v1/clubs/{club_id}/meetings", tags=["meetings"])


@router.get("", status_code=status.HTTP_200_OK)
async def get_meetings(
    club_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> list[MeetingResponse]:
    club_result = await db.execute(select(Club).where(Club.id == club_id))
    if club_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Club not found", "code": "CLUB_NOT_FOUND"},
        )

    meetings_result = await db.execute(select(Meeting).where(Meeting.club_id == club_id))
    meetings_db = meetings_result.scalars().all()

    if not meetings_db:
        return []

    # Load all attendees for all meetings in a single query — eliminates N+1
    meeting_ids = [m.id for m in meetings_db]
    attendees_result = await db.execute(
        select(MeetingAttendee).where(MeetingAttendee.meeting_id.in_(meeting_ids))
    )
    all_attendees = attendees_result.scalars().all()

    # Group attendees by meeting_id
    attendees_by_meeting: dict[uuid.UUID, list[MeetingAttendee]] = defaultdict(list)
    for a in all_attendees:
        attendees_by_meeting[a.meeting_id].append(a)

    return [
        MeetingResponse(
            id=str(m.id),
            clubId=str(m.club_id),
            title=m.title,
            date=m.date.isoformat(),
            attendees=[str(a.user_id) for a in attendees_by_meeting[m.id]],
        )
        for m in meetings_db
    ]
