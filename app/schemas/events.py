from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class AfterMeetingVenueSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=1000)
    lat: float | None = None
    lng: float | None = None


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    clubId: str
    clubName: str
    organizerId: str
    title: str
    description: str | None
    date: datetime | str
    city: str
    address: str | None
    lat: float | None
    lng: float | None
    status: str
    cancelledAt: datetime | str | None
    coverUrl: str | None = None
    bookTitle: str | None = None
    theme: str | None
    tags: list[str] = []
    durationMinutes: int | None
    afterMeetingVenue: AfterMeetingVenueSchema | None
    attendeeCount: int
    isAttending: bool
    hasWinner: bool = False
    winnerId: str | None = None
    winnerName: str | None = None
    googleBookId: str | None = None


class CreateEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    date: datetime
    city: str = Field(min_length=1, max_length=100)
    address: str | None = Field(default=None, max_length=300)
    coverUrl: str | None = Field(default=None, max_length=500)
    bookTitle: str | None = Field(default=None, max_length=300)
    theme: str | None = Field(default=None, max_length=200)
    tags: list[Annotated[str, Field(max_length=50)]] = Field(default=[], max_length=20)
    durationMinutes: int | None = Field(default=None, ge=1, le=480)
    afterMeetingVenue: AfterMeetingVenueSchema | None = None
    googleBookId: str | None = Field(default=None, max_length=50)


class EventUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    date: datetime | None = None
    city: str | None = Field(default=None, min_length=1, max_length=100)
    address: str | None = Field(default=None, max_length=300)
    lat: float | None = None
    lng: float | None = None
    theme: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = None
    cover_url: str | None = None
    duration_minutes: int | None = None
    after_meeting_venue: AfterMeetingVenueSchema | None = None
    has_winner: bool | None = None
    google_book_id: str | None = None


class SetWinnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    winner_id: UUID


class RescheduleEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    newDate: AwareDatetime
    newAddress: str | None = None
    newCity: str | None = None


class AttendEventResponse(BaseModel):
    attendeeCount: int
    autoJoined: bool = False
