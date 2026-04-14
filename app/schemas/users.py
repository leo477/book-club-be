from typing import Literal

from pydantic import BaseModel


class UpdateProfileRequest(BaseModel):
    displayName: str | None = None


class UpdateRoleRequest(BaseModel):
    role: Literal["user", "organizer"]


class UpdateSocialsRequest(BaseModel):
    telegram: str | None = None
    instagram: str | None = None
    twitter: str | None = None
    linkedin: str | None = None
    github: str | None = None
    goodreads: str | None = None


class UpdateSocialsVisibilityRequest(BaseModel):
    socialsPublic: bool


class UserStatsResponse(BaseModel):
    clubsJoined: int
    quizzesTaken: int
    quizWins: int
    likesReceived: int
    booksRead: int
