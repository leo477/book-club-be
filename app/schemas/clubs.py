from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.schemas.events import AfterMeetingVenueSchema


class ClubResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    coverUrl: str | None
    organizerId: str
    isPublic: bool
    memberCount: int
    memberPreviews: list[str] = []
    createdAt: datetime | str
    status: str = "active"
    city: str | None = None
    nextMeetingDate: datetime | str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    theme: str | None = None
    currentBook: str | None = None
    tags: list[str] = []
    meetingDurationMinutes: int | None = None
    afterMeetingVenue: AfterMeetingVenueSchema | None = None
    cancelledAt: datetime | str | None = None
    currentChampion: dict[str, Any] | None = None


class MemberStatRow(BaseModel):
    userId: str
    displayName: str
    avatarUrl: str | None
    count: int


class EventAttendanceStat(BaseModel):
    eventId: str
    title: str
    date: datetime
    attendeeCount: int


class ClubStatsResponse(BaseModel):
    topActive: list[MemberStatRow]
    topWinners: list[MemberStatRow]
    recentAttendance: list[EventAttendanceStat]


class CreateClubRequest(BaseModel):
    name: str
    description: str | None = None
    isPublic: bool = True
    coverUrl: str | None = None
    city: str | None = None
    tags: list[str] = []
    meetingDurationMinutes: int | None = None
    afterMeetingVenue: AfterMeetingVenueSchema | None = None


class UpdateClubRequest(BaseModel):
    # M-8: all fields optional so exclude_unset=True gives true PATCH semantics
    name: str | None = None
    description: str | None = None
    isPublic: bool | None = None
    city: str | None = None
    coverUrl: str | None = None


class RescheduleMeetingRequest(BaseModel):
    newDate: AwareDatetime


class BanRequest(BaseModel):
    duration: Literal[1, 3, 5, "permanent"]


class BanResponse(BaseModel):
    userId: str
    clubId: str
    bannedAt: datetime | str
    duration: str
    bannedBy: str


class MemberResponse(BaseModel):
    userId: str
    displayName: str
    avatarUrl: str | None
    role: str
    socials: dict[str, str] | None
    socialsPublic: bool
