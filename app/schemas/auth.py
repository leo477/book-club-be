from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


def build_socials(user: Any) -> dict[str, str | None]:
    return {
        "telegram": user.telegram,
        "instagram": user.instagram,
        "twitter": user.twitter,
        "linkedin": user.linkedin,
        "github": user.github,
        "goodreads": user.goodreads,
    }


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    email: str
    displayName: str
    role: str
    avatarUrl: str | None = None
    createdAt: str
    socialsPublic: bool
    socials: dict[str, str | None]

    # noinspection PyMethodDecoratorAdapted
    @model_validator(mode="before")
    @classmethod
    def build_from_orm(cls, v: Any) -> Any:
        if hasattr(v, "display_name"):
            return {
                "id": str(v.id),
                "email": v.email,
                "displayName": v.display_name,
                "role": v.role,
                "avatarUrl": v.avatar_url,
                "createdAt": v.created_at.isoformat() if v.created_at else "",
                "socialsPublic": v.socials_public,
                "socials": build_socials(v),
            }
        return v


class AuthResponse(BaseModel):
    user: UserProfileResponse
    accessToken: str
    refreshToken: str


class TokenData(BaseModel):
    user_id: str
