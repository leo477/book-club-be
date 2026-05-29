from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.schemas.events import AfterMeetingVenueSchema


class ChampionInfo(BaseModel):
    userId: str
    displayName: str
    avatarUrl: str | None = None
    wins: int


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
    currentChampion: ChampionInfo | None = None


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


class MonthlyStatRow(BaseModel):
    month: str  # "YYYY-MM"
    count: int


class ClubStatsResponse(BaseModel):
    topActive: list[MemberStatRow]
    topWinners: list[MemberStatRow]
    recentAttendance: list[EventAttendanceStat]
    # Extended fields (Feature 7)
    totalMembers: int
    totalEvents: int
    totalMessages: int
    memberGrowth: list[MonthlyStatRow]
    eventFrequency: list[MonthlyStatRow]
    bannedUsersCount: int
    upcomingEventsCount: int


class CreateClubRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    isPublic: bool = True
    coverUrl: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=100)
    tags: list[Annotated[str, Field(max_length=50)]] = Field(default=[], max_length=20)
    meetingDurationMinutes: int | None = Field(default=None, ge=1, le=480)
    afterMeetingVenue: AfterMeetingVenueSchema | None = None


class UpdateClubRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # M-8: all fields optional so exclude_unset=True gives true PATCH semantics
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    isPublic: bool | None = None
    city: str | None = Field(default=None, max_length=100)
    coverUrl: str | None = Field(default=None, max_length=500)


class RescheduleMeetingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    newDate: AwareDatetime


class BanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
