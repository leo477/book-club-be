from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict


class AfterMeetingVenueSchema(BaseModel):
    name: str
    address: str
    description: str | None = None
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


class CreateEventRequest(BaseModel):
    title: str
    description: str | None = None
    date: datetime
    city: str
    address: str | None = None
    coverUrl: str | None = None
    bookTitle: str | None = None
    theme: str | None = None
    tags: list[str] = []
    durationMinutes: int | None = None
    afterMeetingVenue: AfterMeetingVenueSchema | None = None


class EventUpdatePayload(BaseModel):
    title: str | None = None
    description: str | None = None
    date: datetime | None = None
    city: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    theme: str | None = None
    tags: list[str] | None = None
    cover_url: str | None = None
    duration_minutes: int | None = None
    after_meeting_venue: AfterMeetingVenueSchema | None = None


class RescheduleEventRequest(BaseModel):
    newDate: AwareDatetime
    newAddress: str | None = None
    newCity: str | None = None


class AttendEventResponse(BaseModel):
    attendeeCount: int
    autoJoined: bool
