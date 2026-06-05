from pydantic import BaseModel


class RoutePoint(BaseModel):
    lat: float
    lng: float


class WalkingRouteResponse(BaseModel):
    path: list[RoutePoint]
