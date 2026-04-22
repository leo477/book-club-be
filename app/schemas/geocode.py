from pydantic import BaseModel


class GeocodeSuggestion(BaseModel):
    label: str
    city: str | None = None
    country: str | None = None
    lat: float
    lng: float
