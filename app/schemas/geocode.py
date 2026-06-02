from pydantic import BaseModel


class GeocodeSuggestion(BaseModel):
    label: str
    city: str | None = None
    country: str | None = None
    lat: float | None = None
    lng: float | None = None
    place_id: str | None = None
