from typing import Literal

from pydantic import BaseModel, ConfigDict

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
    createdAt: str
    status: str = "active"
    city: str | None = None
    nextMeetingDate: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    theme: str | None = None
    currentBook: str | None = None
    tags: list[str] = []
    meetingDurationMinutes: int | None = None
    afterMeetingVenue: AfterMeetingVenueSchema | None = None
    cancelledAt: str | None = None


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
