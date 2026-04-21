from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class AfterMeetingVenueSchema(BaseModel):
    name: str
    address: str
    description: str | None = None
    lat: float | None = None
    lng: float | None = None


class CurrentBookSchema(BaseModel):
    title: str
    author: str
    description: str


class MeetingHistoryItemSchema(BaseModel):
    id: str
    date: str
    status: Literal["held", "cancelled", "rescheduled"]
    notes: str | None = None


class ClubResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    coverUrl: str | None
    organizerId: str
    isPublic: bool
    memberCount: int
    createdAt: str
    city: str
    nextMeetingDate: str | None
    address: str | None
    lat: float | None
    lng: float | None
    theme: str | None
    currentBook: CurrentBookSchema | None = None
    memberPreviews: list[str] = []
    status: str
    cancelledAt: str | None = None
    tags: list[str] = []
    meetingDurationMinutes: int | None
    afterMeetingVenue: AfterMeetingVenueSchema | None
    meetingHistory: list[MeetingHistoryItemSchema] = []


class CreateClubRequest(BaseModel):
    name: str
    description: str | None = None
    isPublic: bool = True
    city: str
    tags: list[str] = []
    meetingDurationMinutes: int | None = None
    afterMeetingVenue: AfterMeetingVenueSchema | None = None
    nextMeetingDate: datetime | None = None


class RescheduleRequest(BaseModel):
    newDate: str


class BanRequest(BaseModel):
    duration: Literal[1, 3, 5, "permanent"]


class BanResponse(BaseModel):
    userId: str
    clubId: str
    bannedAt: str
    duration: str
    bannedBy: str


class MemberResponse(BaseModel):
    userId: str
    displayName: str
    avatarUrl: str | None
    role: str
    socials: dict[str, str] | None
    socialsPublic: bool
