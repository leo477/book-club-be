from pydantic import BaseModel, ConfigDict, model_validator


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
    def build_from_orm(cls, v: object) -> object:
        if hasattr(v, "display_name"):
            return {
                "id": str(v.id),  # type: ignore[attr-defined]
                "email": v.email,  # type: ignore[attr-defined]
                "displayName": v.display_name,
                "role": v.role,  # type: ignore[attr-defined]
                "avatarUrl": v.avatar_url,  # type: ignore[attr-defined]
                "createdAt": v.created_at.isoformat() if v.created_at else "",  # type: ignore[attr-defined]
                "socialsPublic": v.socials_public,  # type: ignore[attr-defined]
                "socials": {
                    "telegram": v.telegram,  # type: ignore[attr-defined]
                    "instagram": v.instagram,  # type: ignore[attr-defined]
                    "twitter": v.twitter,  # type: ignore[attr-defined]
                    "linkedin": v.linkedin,  # type: ignore[attr-defined]
                    "github": v.github,  # type: ignore[attr-defined]
                    "goodreads": v.goodreads,  # type: ignore[attr-defined]
                },
            }
        return v


class AuthResponse(BaseModel):
    user: UserProfileResponse
    accessToken: str


class TokenData(BaseModel):
    user_id: str
