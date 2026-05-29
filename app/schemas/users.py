from typing import Literal

from pydantic import BaseModel, ConfigDict


class UpdateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    displayName: str | None = None


class UpdateRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "organizer"]


class UpdateSocialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    telegram: str | None = None
    instagram: str | None = None
    twitter: str | None = None
    linkedin: str | None = None
    github: str | None = None
    goodreads: str | None = None


class UpdateSocialsVisibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    socialsPublic: bool


class UserStatsResponse(BaseModel):
    clubsJoined: int
    quizzesTaken: int
    quizWins: int
    likesReceived: int
    booksRead: int
