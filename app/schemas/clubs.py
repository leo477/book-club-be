from typing import Literal

from pydantic import BaseModel, ConfigDict


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


class CreateClubRequest(BaseModel):
    name: str
    description: str | None = None
    isPublic: bool = True
    coverUrl: str | None = None


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
