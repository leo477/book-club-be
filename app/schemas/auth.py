from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    displayName: str = Field(min_length=1, max_length=100)
    role: Literal["user", "organizer"] = "user"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    email: str
    displayName: str
    role: str
    avatarUrl: str | None = None
    createdAt: str
    socialsPublic: bool
    socials: dict

    @model_validator(mode="before")
    @classmethod
    def build_from_orm(cls, v: object) -> object:
        if hasattr(v, "display_name"):
            return {
                "id": str(v.id),  # type: ignore[union-attr]
                "email": v.email,  # type: ignore[union-attr]
                "displayName": v.display_name,  # type: ignore[union-attr]
                "role": v.role,  # type: ignore[union-attr]
                "avatarUrl": v.avatar_url,  # type: ignore[union-attr]
                "createdAt": v.created_at.isoformat() if v.created_at else "",  # type: ignore[union-attr]
                "socialsPublic": v.socials_public,  # type: ignore[union-attr]
                "socials": {
                    "telegram": v.telegram,  # type: ignore[union-attr]
                    "instagram": v.instagram,  # type: ignore[union-attr]
                    "twitter": v.twitter,  # type: ignore[union-attr]
                    "linkedin": v.linkedin,  # type: ignore[union-attr]
                    "github": v.github,  # type: ignore[union-attr]
                    "goodreads": v.goodreads,  # type: ignore[union-attr]
                },
            }
        return v


class AuthResponse(BaseModel):
    user: UserProfileResponse
    accessToken: str


class TokenData(BaseModel):
    user_id: str
