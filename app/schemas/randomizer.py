from pydantic import BaseModel


class CandidateSchema(BaseModel):
    userId: str
    displayName: str
    avatarUrl: str | None = None


class RandomizerSessionResponse(BaseModel):
    id: str
    clubId: str
    createdBy: str
    purpose: str
    candidates: list[CandidateSchema]
    result: CandidateSchema | None
    createdAt: str  # ISO


class CreateRandomizerSessionRequest(BaseModel):
    purpose: str
    candidates: list[CandidateSchema]
    result: CandidateSchema | None = None
