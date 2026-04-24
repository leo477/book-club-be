from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
    date: str
    city: str
    address: str | None
    lat: float | None
    lng: float | None
    status: str
    cancelledAt: str | None
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
    theme: str | None = None
    tags: list[str] = []
    durationMinutes: int | None = None
    afterMeetingVenue: AfterMeetingVenueSchema | None = None


class RescheduleEventRequest(BaseModel):
    newDate: str
    newAddress: str | None = None
    newCity: str | None = None
