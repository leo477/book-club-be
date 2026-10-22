from pydantic import BaseModel, ConfigDict, Field

MAX_CANDIDATES = 1000


class CandidateSchema(BaseModel):
    userId: str
    displayName: str
    avatarUrl: str | None = None


class CandidateIn(BaseModel):
    userId: str = Field(max_length=36)
    displayName: str = Field(max_length=100)
    avatarUrl: str | None = Field(default=None, max_length=2048)


class RandomizerSessionResponse(BaseModel):
    id: str
    clubId: str
    createdBy: str
    purpose: str
    candidates: list[CandidateSchema]
    result: CandidateSchema | None
    createdAt: str  # ISO


class CreateRandomizerSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(max_length=200)
    candidates: list[CandidateIn] = Field(max_length=MAX_CANDIDATES)
    result: CandidateIn | None = None
