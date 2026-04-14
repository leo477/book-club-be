import uuid
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


@router.get("", response_model=list[MeetingResponse], status_code=status.HTTP_200_OK)
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

    responses: list[MeetingResponse] = []
    for meeting in meetings_db:
        attendees_result = await db.execute(select(MeetingAttendee).where(MeetingAttendee.meeting_id == meeting.id))
        attendees = attendees_result.scalars().all()
        responses.append(
            MeetingResponse(
                id=str(meeting.id),
                clubId=str(meeting.club_id),
                title=meeting.title,
                date=meeting.date.isoformat(),
                attendees=[str(a.user_id) for a in attendees],
            )
        )

    return responses
